import hydra
from omegaconf import DictConfig, OmegaConf
from torch import optim
import procedures
import torch
import os
from nets.yolo_forw import YOLOForw
from procedures.init_dataset import get_dataloaders
from procedures.train_one_epoch import train_one_epoch
from procedures.valid_one_epoch import valid_one_epoch
from procedures.test_one_epoch import test_one_epoch
from procedures.eval_results import eval_results,save_results
from procedures.initialize import get_model
import logging
from multiprocessing import Pool, current_process

log = logging.getLogger(__name__)

@hydra.main(config_path="hydra",config_name="hyperopt")
def main(cfg: DictConfig) -> None:
    os.environ['owd'] = hydra.utils.get_original_cwd()

    torch.cuda.empty_cache()
    torch.cuda.set_device(cfg.job_id % cfg.gpus)
    dset_config=cfg['dataset']
    mAP = 0
    last_epoch=0
    rank= (cfg.job_id % cfg.gpus)

    #model,optimizer
    model,optimizer,_,last_epoch=get_model(cfg)
    #dataloaders
    train_loader,test_loader = get_dataloaders(cfg)           
    
    #criterion
    criterion = YOLOForw(cfg['yolo'])

    epochs=1
    batch_loss = torch.zeros(1)
    for i in range(epochs):
        if cfg.only_test is False:
            train_one_epoch(train_loader,model,optimizer,criterion,rank)

        if cfg.metrics =='mAP':
            results=test_one_epoch(test_loader,model,criterion)
            save_results(results,rank)
            if rank==0:
                mAP=eval_results(i+last_epoch,dset_config['dset_name'],dset_config['val_annotations'])
                print(f'map is {mAP}')
                return mAP
        else:
            batch_loss = valid_one_epoch(test_loader,model,criterion,rank)
            if torch.isnan(batch_loss):
                batch_loss = torch.tensor([1e6],device='cuda')
            if rank==0:
                print(f'batch_loss is {batch_loss}')
                return batch_loss.item()
        

if __name__=='__main__':
    main()