from torchvision.datasets.vision import VisionDataset
from lvis import LVIS
import os
from typing import Any, Callable, List, Optional, Tuple
from utilities import helper
from skimage import io
import random
import cv2
import numpy as np

class LVISDetection(VisionDataset):
    """`MS Coco Detection <https://cocodataset.org/#detection-2016>`_ Dataset.

    Args:
        root (string): Root directory where images are downloaded to.
        annFile (string): Path to json annotation file.
        transform (callable, optional): A function/transform that  takes in an PIL image
            and returns a transformed version. E.g, ``transforms.ToTensor``
        target_transform (callable, optional): A function/transform that takes in the
            target and transforms it.
        transforms (callable, optional): A function/transform that takes input sample and its target as entry
            and returns a transformed version.

    """

    def __init__(
            self,
            root: str,
            annFile: str,
            subset:float,
            transform: Optional[Callable] = None,
            target_transform: Optional[Callable] = None,
            transforms: Optional[Callable] = None,
    ) -> None:
        super(LVISDetection, self).__init__(root, transforms, transform, target_transform)
        self.lvis = LVIS(annFile)
        self.ids = list(sorted(self.lvis.imgs.keys()))
        
        self.ids = sorted(random.sample(self.ids, int(len(self.ids)*subset)))

    def __getitem__(self, index: int) -> Tuple[Any, Any]:
        """
        Args:
            index (int): Index

        Returns:
            tuple: Tuple (image, target). target is the object returned by ``lvis.loadAnns``.
        """
        lvis = self.lvis
        img_id = self.ids[index]
        ann_ids = lvis.get_ann_ids(img_ids=[img_id])
        target = lvis.load_anns(ann_ids)
        targets={}
        path = lvis.load_imgs([img_id])[0]['coco_url']
        path=path.split('/')[-2:]
        path='/'.join(path)

        img = io.imread(os.path.join(self.root, path))
        if (img.shape[-1]!=3):
            img = cv2.cvtColor(img,cv2.COLOR_GRAY2RGB)

        if target==[]:
            return None

        targets['bbox'] = np.array([t['bbox'] for t in target])
        targets['category_id'] = np.array([t['category_id'] for t in target],dtype=np.int64)
        targets['area']=np.array([t['area'] for t in target])
        targets['image_id']=np.array(target[0]['image_id'],dtype=np.int64)

        sample=img,targets
        
        if self.transform:
            sample = self.transform(sample)
            
        return sample


    def __len__(self) -> int:
        return len(self.ids)