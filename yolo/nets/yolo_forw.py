import torch
from torch._C import device
import torch.nn as nn
import numpy as np
import math
from utilities import custom, helper

from torchvision.ops import boxes


class YOLOForw(nn.Module):
    def __init__(self, cfg):
        super(YOLOForw, self).__init__()
        self.anchors = cfg['anchors']
        self.num_anchors = len(self.anchors)
        self.num_classes = cfg['classes']
        self.bbox_attrs = 5 + self.num_classes
        self.img_size = cfg['img_size']

        self.ignore_threshold = cfg['ignore_threshold']
        self.lambda_iou = cfg['lambda_iou']

        self.lambda_xy = cfg['lambda_xy']
        self.lambda_wh = cfg['lambda_wh']
        self.lambda_conf = cfg['lambda_conf']
        self.lambda_no_conf = cfg['lambda_no_conf']
        self.lambda_cls = cfg['lambda_cls']


        self.device= torch.device('cuda')
        self.iou_type = cfg['iou_type']
        self.wh_loss = nn.MSELoss()
        self.xy_loss = nn.BCEWithLogitsLoss()
        self.conf_loss = nn.BCEWithLogitsLoss()
        self.class_loss = nn.BCEWithLogitsLoss()

    def forward(self, input, targets=None):
        raw_pred=[]
        cxypwh =[]
        inw_inh=[]
        strides=[]
        for k,input in enumerate(input):
            bs = input.size(0)
            in_h = input.size(2)
            in_w = input.size(3)
            stride_h = self.img_size / in_h
            stride_w = self.img_size / in_w
            scaled_anchors = torch.tensor([(a_w / stride_w, a_h / stride_h) for a_w, a_h in self.anchors[k]],device=self.device)

            prediction = input.view(bs,3,self.bbox_attrs, in_h, in_w).permute(0, 3, 4, 1, 2).contiguous()
            # prediction = prediction.permute(0,2,3,1, 4).contiguous()
            prediction = torch.reshape(prediction,[bs,-1,self.bbox_attrs]).contiguous()
            grid_x = torch.linspace(0, in_w-1, in_w,device=self.device).repeat(in_w, 1).repeat(3, 1, 1).permute(1,2,0) + 0.5
            grid_y = torch.linspace(0, in_h-1, in_h,device=self.device).repeat(in_h, 1).t().repeat(3, 1, 1).permute(1,2,0) + 0.5
            grid_x=torch.reshape(grid_x,[-1])/in_w
            grid_y=torch.reshape(grid_y,[-1])/in_h
            anchor_w = scaled_anchors[:,0]/in_w
            anchor_w = anchor_w.repeat(1, in_h * in_w)
            anchor_w=torch.reshape(anchor_w,[-1])
            anchor_h = scaled_anchors[:,1]/in_h
            anchor_h = anchor_h.repeat(1, in_h * in_w)
            anchor_h=torch.reshape(anchor_h,[-1])
            cxypwh.append(torch.stack((grid_x,grid_y,anchor_w,anchor_h),axis=1))
            raw_pred.append(prediction)
            inw_inh.append(torch.ones(grid_y.shape,device=self.device)*in_w)
        inw_inh=torch.cat(inw_inh,axis=0)
        raw_pred=torch.cat(raw_pred,axis=1)
        cxypwh=torch.cat(cxypwh,axis=0)

        if targets is not None:
            tgt,tcls,obj_mask, noobj_mask = self.get_target(targets, cxypwh, inw_inh, ignore_threshold=self.ignore_threshold)
            final=torch.cat([raw_pred[k,i] for k,i in enumerate(obj_mask)])
            true_pred,gt = self.transform_pred(final,tgt,cxypwh,inw_inh,obj_mask)
            iou_loss = self.lambda_iou *  (1 - helper.bbox_iou(true_pred,gt,self.iou_type)).mean()
            loss_xy = self.lambda_xy * self.xy_loss(final[:,:2],tgt[:,:2])
            loss_wh = self.lambda_wh * self.wh_loss(final[:,2:4],tgt[:,2:4])
            pos_conf_loss = self.lambda_conf * self.conf_loss(final[:,4],torch.ones(final.shape[0],device=self.device))
            neg_conf_loss = self.lambda_no_conf * self.conf_loss(raw_pred[noobj_mask],torch.zeros(raw_pred[noobj_mask].shape,device=self.device))
            class_loss = self.lambda_cls * self.class_loss(final[:,5:],tcls)
            loss = loss_xy + loss_wh + iou_loss + pos_conf_loss + neg_conf_loss + class_loss

            return loss,loss_xy.detach(),loss_wh.detach(),iou_loss.detach(),pos_conf_loss.detach(),neg_conf_loss.detach(),class_loss.detach()
        else:
            strides = (self.img_size / inw_inh).unsqueeze(1)
            inw_inh = inw_inh.unsqueeze(1)
            xy=(torch.sigmoid(raw_pred[...,0:2]) + cxypwh[:,:2] * inw_inh - 0.5)*strides
            wh=torch.exp(raw_pred[...,2:4]) * cxypwh[:,2:4] * inw_inh * strides
            conf=torch.sigmoid(raw_pred[:,:,4:5])
            cls=torch.sigmoid(raw_pred[:,:,5:])
            output = torch.cat((xy,wh,conf,cls),axis=2)

            return output.data

    def get_target(self, targets, cxypwh, inw_inh, ignore_threshold=0.5):
        obj_mask=[]
        noobj_mask=[]
        tgt=[]
        tcls=[]
        for b,target in enumerate(targets):
            bbox=target['bbox']
            tcls.append(torch.nn.functional.one_hot(target['category_id'],self.num_classes).float())
            abs_box=helper.get_abs_coord(bbox)
            iou=boxes.box_iou(abs_box,helper.get_abs_coord(cxypwh))
            iou_mask=iou.max(axis=1)[1]
            iou_values=iou.max(axis=1)[0]
            gt=cxypwh[iou_mask].cuda()
            in_wh=inw_inh[iou_mask]
            gx=(bbox[:,0] * in_wh)-(bbox[:,0] * in_wh).long()
            gy=(bbox[:,1] * in_wh)-(bbox[:,1] * in_wh).long()
            gw=torch.log(bbox[:,2]/gt[:,2] + 1e-16)
            gh=torch.log(bbox[:,3]/gt[:,3] + 1e-16)

            tgt.append(torch.stack([gx,gy,gw,gh],axis=1))
            ignore_mask=((iou>ignore_threshold).sum(axis=0))>0
            noobj_mask.append(ignore_mask)
            obj_mask.append(iou_mask)

        noobj_mask=torch.stack(noobj_mask,axis=0)
        tgt=torch.cat(tgt,axis=0)
        tcls=torch.cat(tcls,axis=0)
        return tgt,tcls,obj_mask, noobj_mask


    def transform_pred(self,raw_pred,tgt,cxypwh,inw_inh,mask):
        cxypwh=torch.cat([cxypwh[i] for k,i in enumerate(mask)])
        inw_inh=torch.cat([inw_inh[i] for k,i in enumerate(mask)])
        inw_inh=inw_inh.unsqueeze(1)
        strides = self.img_size / inw_inh

        xy=(torch.sigmoid(raw_pred[:,0:2]) + cxypwh[:,:2] * inw_inh - 0.5)*strides
        wh=torch.exp(raw_pred[:,2:4]) * cxypwh[:,2:4] * inw_inh * strides
        conf=torch.sigmoid(raw_pred[:,4:5])
        cls=torch.sigmoid(raw_pred[:,5:])
        true_pred = torch.cat((xy,wh,conf,cls),axis=1)
        
        xy = (tgt[:,:2] + cxypwh[:,:2] * inw_inh - 0.5) * strides
        wh = torch.exp(tgt[:,2:4]) * cxypwh[:,2:4] * inw_inh * strides
        gt = torch.cat([xy,wh],axis=1)

        return true_pred,gt