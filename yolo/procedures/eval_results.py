from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
from lvis import LVISEval
import os
import json
import hydra
import pickle
import itertools
import random


def save_partial_results(results,rank):
    path='bbox_results/temp_res'
    if not os.path.exists(path):
        os.makedirs(path)

    temp_name=os.path.join(path,'{}.json'.format(rank))
    with open(temp_name,'wb') as f:
        pickle.dump(results, f)
    


def eval_partial_results(epoch,dset_name,validation_path):
    results=[]
    mAP = -1
    directory = 'bbox_results/temp_res'
    for filename in os.listdir(directory):
        if filename.endswith(".json"):
            temp_name = os.path.join(directory, filename)
            with open(temp_name, 'rb') as f:
                results=list(itertools.chain(results, pickle.load(f)))
                
    cwd = os.getenv('owd')  
    validation_path=os.path.join(cwd,validation_path)
    
    if not os.path.exists(f'bbox_results/{dset_name}/'):
        os.makedirs(f'bbox_results/{dset_name}/')

    json.dump(results, open(f'./bbox_results/{dset_name}/results_{epoch}.json', 'w'), indent=4)
    resFile=f'./bbox_results/{dset_name}/results_{epoch}.json'

    if (dset_name=='coco') | (dset_name=='drones'):
        cocoGt=COCO(validation_path)
        try:
            cocoDt=cocoGt.loadRes(resFile)
        except IndexError:
            print('empty list return zero map')
            return 0
        cocoDt.loadAnns()

        #  running evaluation
        cocoEval = COCOeval(cocoGt,cocoDt,'bbox')
        cocoEval.evaluate()
        cocoEval.accumulate()
        cocoEval.summarize()

        mAP=cocoEval.stats[0]

    elif(dset_name=='lvis'):
    
        lvis_eval = LVISEval(validation_path, resFile, 'bbox')
        lvis_eval.run()
        metrics=lvis_eval.get_results()
        lvis_eval.print_results()
        mAP=metrics['AP']

    return (mAP)


def eval_results(results,dset_name,validation_path):

    cwd = os.getenv('owd')  
    validation_path=os.path.join(cwd,validation_path)

    if not os.path.exists(f'bbox_results/{dset_name}/'):
        os.makedirs(f'bbox_results/{dset_name}/')
    
    rid = (random.randint(0, 1000000))
    json.dump(results, open(f'./bbox_results/{dset_name}/results_{rid}.json', 'w'), indent=4)
    resFile=f'./bbox_results/{dset_name}/results_{rid}.json'

    if (dset_name=='coco') | (dset_name=='drones'):
        cocoGt=COCO(validation_path)
        try:
            cocoDt=cocoGt.loadRes(resFile)
        except IndexError:
            print('empty list return zero map')
            return 0
        cocoDt.loadAnns()

        #  running evaluation
        cocoEval = COCOeval(cocoGt,cocoDt,'bbox')
        cocoEval.evaluate()
        cocoEval.accumulate()
        cocoEval.summarize()

        mAP=cocoEval.stats[0]

    elif(dset_name=='lvis'):
        try:
            lvis_eval = LVISEval(validation_path, resFile, 'bbox')
        except IndexError:
            print('empty list return zero map')
            return 0
        lvis_eval.run()
        metrics=lvis_eval.get_results()
        lvis_eval.print_results()
        mAP=metrics['AP']
    
    os.remove(resFile)
    
    return (mAP)