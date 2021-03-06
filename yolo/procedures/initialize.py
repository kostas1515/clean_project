from torch import optim
from nets.yolohead import YoloHead
import os
import torch
from torch import nn
from collections import OrderedDict
from apex.parallel import DistributedDataParallel as DDP
from apex import amp
import time
import apex

def save_model(model,optimizer,scheduler,metrics,epoch,name):
    if not os.path.exists('checkpoints/'):
        os.makedirs('checkpoints/')
    
    checkpoint=f'checkpoints/{name}.tar'

    torch.save({'epoch': epoch,
                'model_state_dict': model.module.state_dict() if type(model) is not DDP else model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'scheduler_state_dict': scheduler.state_dict(),
                'optimizer_name':optimizer.name,
                'scheduler_name':scheduler.name,
                'metrics': metrics
                }, checkpoint)


def get_model(cfg):
    model = YoloHead(cfg)
    model = model.cuda(cfg.rank)
    if cfg.batch_norm_sync is True:
        model = apex.parallel.convert_syncbn_model(model)
    epoch = 0
    metrics={'mAP':None,'val_loss':None}
    optimizer = None

    if cfg['optimizer']['name'] == 'sgd':
        optimizer = optim.SGD(model.parameters(), lr=cfg['optimizer']['lr'], momentum=cfg['optimizer']['momentum'],weight_decay=cfg['optimizer']['weight_decay'])
        
    elif cfg['optimizer']['name'] == 'adam':
        optimizer = optim.Adam(model.parameters(), lr=cfg['optimizer']['lr'],weight_decay=cfg['optimizer']['weight_decay'])
        
    
    model, optimizer = amp.initialize(model,optimizer, 
                                      opt_level=cfg.apex_opt)                                  
    optimizer.name = cfg['optimizer']['name']
    try:
        model = DDP(model)
    except AssertionError:
        print("something went wrong with DDP, use standart model")
        pass

    return model,optimizer,metrics,epoch



def load_checkpoint(model,optimizer,cfg,scheduler=None):
    exp_name=cfg.experiment.name
    map_location="cuda:{}".format(cfg.rank)
    if os.path.exists(os.path.join('../',exp_name,'checkpoints/')):
        cp_name=cfg['experiment']['cp']
        checkpoint = torch.load(f'checkpoints/{cp_name}.tar',map_location=map_location)
        try:
            model.load_state_dict(checkpoint['model_state_dict'])
            print(f'checkpoint loaded from:{cfg.experiment.name}')
        except RuntimeError:
            new_state_dict = OrderedDict()
            for k, v in checkpoint['model_state_dict'].items():
                name = k[7:] # remove 'module.' of dataparallel
                new_state_dict[name]=v
            model.load_state_dict(new_state_dict)
        optimizer_name=checkpoint['optimizer_name']
        try:
            scheduler_name=checkpoint['scheduler_name']
        except:
            pass
        epoch=checkpoint['epoch'] + 1
        metrics=checkpoint['metrics']
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        optimizer.name=optimizer_name
        try:
            scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
            scheduler.name=scheduler_name
        except:
            print('Warning: could not load scheduler')
            pass
        return metrics,epoch
    elif cfg.pretrained_head is True:
        print('pretrained loaded')
        owd = os.getenv('owd')
        try:
            path= os.path.join(owd,'weights/official_yolov3_weights_pytorch.pth')
            checkpoint=torch.load(path,map_location=map_location)
            model.load_state_dict(checkpoint)
        except RuntimeError:
            path= os.path.join(owd,'weights/yolov3_orig.pth')
            checkpoint=torch.load(path,map_location=map_location)
            model.load_state_dict(checkpoint)
    else:
        print('checkpoint not found, returning random model')
    
    

    return {'mAP':None,'val_loss':None}, 0



def get_scheduler(optimizer,cfg):
    config=cfg.scheduler
    if config.name == 'reduce_on_plateau':
        mode = config.mode
        factor=config.factor
        patience=config.patience
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode,factor,patience)
    elif config.name == 'multistep':
        milestones = config.milestones
        factor=config.factor
        scheduler = optim.lr_scheduler.MultiStepLR(optimizer, milestones=milestones, gamma=factor)
    elif config.name == 'cyclic':
        base_lr=config.base_lr
        max_lr=config.max_lr
        scheduler=optim.lr_scheduler.CyclicLR(optimizer, base_lr=base_lr, max_lr=max_lr)
    else:
        factor=config.factor
        step_size=config.step_size
        scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=step_size, gamma=factor)

    scheduler.name=config.name

    return scheduler
