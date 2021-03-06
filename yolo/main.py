import hydra
from omegaconf import DictConfig
import torch
import os
import torch.distributed as dist
from nets.yolo_forw import YOLOForw
from procedures.init_dataset import get_dataloaders
from procedures.train_one_epoch import train_one_epoch
from procedures.valid_one_epoch import valid_one_epoch
from procedures.test_one_epoch import test_one_epoch
from procedures.eval_results import eval_partial_results,save_partial_results
from procedures.initialize import save_model,get_model,load_checkpoint,get_scheduler
import logging
import torch.multiprocessing as mp
from utilities import helper
from torch.utils.tensorboard import SummaryWriter
import random
import math


def setup(rank, cfg):
    """Initializes distributed process group.
    Arguments:
        rank: the rank of the current process.
        world_size: the total number of processes.
        backend: the backend used for distributed processing.
    """
    os.environ["MASTER_ADDR"] = 'localhost'
    os.environ["MASTER_PORT"] = cfg.master_port
    dist.init_process_group(backend='nccl', init_method='env://', rank=rank, world_size=cfg.gpus)


def cleanup():
    """Cleans up distributed backend resources."""
    dist.destroy_process_group()


@hydra.main(config_path="hydra",config_name="config")
def main(cfg: DictConfig) -> None:
    os.environ['owd'] = hydra.utils.get_original_cwd()
    cfg.master_port=str(cfg.master_port)
    mp.spawn(pipeline, nprocs=cfg.gpus, args=(cfg,), join=True)

def get_logger():
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)
    fh = logging.FileHandler('main.log','a')
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    log.addHandler(fh)
    return log

def pipeline(rank,cfg):
    setup(rank, cfg)
    torch.cuda.set_device(rank)
    cfg.rank=rank
    dset_config=cfg['dataset']
    mAP_best = 0
    val_loss_best = -1000000
    torch.backends.cudnn.benchmark = True
    log=get_logger()
    if rank == 0:
        writer = SummaryWriter('tensorboard')


    #model,optimizer
    model,optimizer,metrics,last_epoch=get_model(cfg)

    #scheduler
    scheduler=get_scheduler(optimizer,cfg)

    #checkpoint
    if cfg.resume is True:
        metrics,last_epoch = load_checkpoint(model,optimizer,cfg,scheduler)
        mAP_best = metrics["mAP"]
        val_loss_best = metrics["val_loss"]
    #dataloaders
    train_loader,test_loader = get_dataloaders(cfg)      
    
    #criterion
    criterion = YOLOForw(cfg).cuda()

    epochs=100
    batch_loss = torch.zeros(1,device='cuda')
    mAP = torch.zeros(1,device='cuda')
    for i in range(epochs):

        avg_losses,avg_stats = train_one_epoch(train_loader,model,optimizer,criterion,i,cfg)
    
        # msg=f"Rank:{rank} gave None Loss"
        # log.warning(msg)
        # avg_losses,avg_stats = 10000 * torch.ones(6,device='cuda'),torch.zeros(5,device='cuda')

        dist.all_reduce(avg_losses, op=torch.distributed.ReduceOp.SUM, async_op=False)
        dist.all_reduce(avg_stats, op=torch.distributed.ReduceOp.SUM, async_op=False)
        avg_losses = avg_losses.cpu().numpy() 
        avg_stats = avg_stats.cpu().numpy() / cfg.gpus

        if cfg.metric =='mAP':
            results=test_one_epoch(test_loader,model,criterion,cfg)
            save_partial_results(results,rank)
            dist.barrier()
            if rank==0:
                mAP=eval_partial_results(i+last_epoch,dset_config['dset_name'],dset_config['val_annotations'])
                print(f'map is {mAP}')
                metrics['mAP']=mAP
                metrics['val_loss']=-1000000
                mAP=torch.tensor(mAP,device='cuda')
                save_model(model,optimizer,scheduler,metrics,i+last_epoch,'last')
                if  metrics['mAP']>mAP_best:
                    print(f'New Best Value:{metrics["mAP"]}!')
                    save_model(model,optimizer,scheduler,metrics,i+last_epoch,'best')
                    mAP_best= metrics['mAP']
        else:
            batch_loss = - valid_one_epoch(test_loader,model,criterion,cfg)
            dist.all_reduce(batch_loss, op=torch.distributed.ReduceOp.SUM, async_op=False)
            if rank==0:
                print(f'batch_loss is {batch_loss}')
                metrics['mAP']=0
                metrics['val_loss']=batch_loss.item()
                save_model(model,optimizer,scheduler,metrics,i+last_epoch,'last')
                if metrics['val_loss']>val_loss_best:
                    print(f'New Best Value:{metrics["val_loss"]}!')
                    save_model(model,optimizer,scheduler,metrics,i+last_epoch,'best')
                    val_loss_best=metrics['val_loss']

        #scheduler step
        if scheduler.name =='reduce_on_plateau':
            if cfg.metric =='mAP':
                dist.broadcast(mAP,0)
                scheduler.step(mAP.item())
            else:
                scheduler.step(batch_loss.item())
        else:
            scheduler.step()   

        if rank==0:
            msg= f'Epoch:{i+last_epoch},Loss is:{avg_losses.sum()}, xy is:{avg_losses[0]},wh is:{avg_losses[1]},iou is:{avg_losses[2]},',f'pos_conf is:{avg_losses[3]}, neg_conf is:{avg_losses[4]},class is:{avg_losses[5]}, mAP is:{metrics["mAP"]}, val_loss is: {metrics["val_loss"]}' 
            stats_msg = {'iou':avg_stats[0],'pos_conf':avg_stats[1],'neg_conf':avg_stats[2],'pos_class':avg_stats[3],'neg_class':avg_stats[4]}
            log.info(msg)
            log.info(stats_msg)
            helper.write_progress_stats(avg_losses,avg_stats,metrics,i+last_epoch)
            writer.add_scalar('Avg Loss/train', avg_losses.sum(),i+last_epoch)
            writer.add_scalar('XY Loss/train', avg_losses[0],i+last_epoch)
            writer.add_scalar('WH Loss/train', avg_losses[1],i+last_epoch)
            writer.add_scalar('IOU Loss/train', avg_losses[2],i+last_epoch)
            writer.add_scalar('Pos_Conf Loss/train', avg_losses[3],i+last_epoch)
            writer.add_scalar('Neg_Conf Loss/train', avg_losses[4],i+last_epoch)
            writer.add_scalar('Class Loss/train', avg_losses[5],i+last_epoch)
            writer.add_scalar('IOU/train', avg_stats[0],i+last_epoch)
            writer.add_scalar('Pos Conf/train', avg_stats[1],i+last_epoch)
            writer.add_scalar('Neg Conf/train', avg_stats[2],i+last_epoch)
            writer.add_scalar('Pos Class/train', avg_stats[3],i+last_epoch)
            writer.add_scalar('Neg Class/train', avg_stats[4],i+last_epoch)
            writer.add_scalar('mAP/valid',metrics['mAP'],i+last_epoch)
            writer.add_scalar('Valid Loss/valid', metrics['val_loss'],i+last_epoch)

    cleanup()

if __name__=='__main__':
    main()