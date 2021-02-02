import hydra
from omegaconf import DictConfig, OmegaConf
from torch import optim
import procedures
import torch
import os
import torch.distributed as dist
from nets.yolo_forw import YOLOForw
from procedures.init_dataset import get_dataloaders
from procedures.train_one_epoch import train_one_epoch
from procedures.valid_one_epoch import valid_one_epoch
from procedures.test_one_epoch import test_one_epoch
from procedures.eval_results import eval_results,save_results
from procedures.initialize import save_model,get_model
import logging

log = logging.getLogger(__name__)

def setup(master_addr: str, master_port: str, rank: int, world_size: int, backend: str):
    """Initializes distributed process group.
    Arguments:
        rank: the rank of the current process.
        world_size: the total number of processes.
        backend: the backend used for distributed processing.
    """
    os.environ["MASTER_ADDR"] = master_addr
    os.environ["MASTER_PORT"] = master_port
    dist.init_process_group(backend=backend, init_method='env://', rank=rank, world_size=world_size)


def cleanup():
    """Cleans up distributed backend resources."""
    dist.destroy_process_group()


@hydra.main(config_path="hydra",config_name="config")
def main(cfg: DictConfig) -> None:
    setup(cfg.master_addr, cfg.master_port, cfg.rank, cfg.world_size, cfg.backend)
    torch.cuda.set_device(cfg.rank)
    dset_config=cfg['dataset']
    mAP_best=0
    mAP = 0
    last_epoch=0

    #model,optimizer
    model,optimizer,mAP_best,last_epoch=get_model(cfg)
    #dataloaders
    train_loader,test_loader = get_dataloaders(cfg)           
    
    #criterion
    criterion = YOLOForw(cfg['yolo'])

    epochs=1
    batch_loss = torch.zeros(1)
    for i in range(epochs):
        if cfg.only_test is False:
            train_one_epoch(train_loader,model,optimizer,criterion,cfg.rank)

        if cfg.metrics =='mAP':
            results=test_one_epoch(test_loader,model,criterion)
            save_results(results,cfg.rank)
            dist.barrier()
            if cfg.rank==0:
                mAP=eval_results(i+last_epoch,dset_config['dset_name'],dset_config['val_annotations'])
                print(f'map is {mAP}')
                return mAP
        else:
            batch_loss = valid_one_epoch(test_loader,model,criterion,cfg.rank)
            dist.all_reduce(batch_loss, op=torch.distributed.ReduceOp.SUM, async_op=False)
            batch_loss = batch_loss / len(test_loader.dataset)
            if cfg.rank==0:
                print(f'batch_loss is {batch_loss}')
                return batch_loss.item()
        
    cleanup()

if __name__=='__main__':
    main()