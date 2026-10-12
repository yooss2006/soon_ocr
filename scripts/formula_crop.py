"""Clean a detected formula crop using connected components, not transcription.

Keep complete ink components mostly inside the detected region. This removes
clipped neighboring Korean strokes and recovers clipped subscripts at its edge.
The source PDF image is never changed.
"""
import cv2
import numpy as np
from PIL import Image


def prepare_formula_crop(image, bbox, focus_bbox=None):
    source=np.asarray(image.convert('RGB'))
    height,width=source.shape[:2]
    x1,y1,x2,y2=bbox
    margin=max(12,round((y2-y1)*.35))
    left=max(0,int(x1)-margin);top=max(0,int(y1)-margin)
    right=min(width,int(x2)+margin+1);bottom=min(height,int(y2)+margin+1)
    gray=cv2.cvtColor(source[top:bottom,left:right],cv2.COLOR_RGB2GRAY)
    core=gray[max(0,int(y1)-top):min(gray.shape[0],int(y2)-top+1),max(0,int(x1)-left):min(gray.shape[1],int(x2)-left+1)]
    if np.median(core)<160:gray=255-gray
    _,ink=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    count,labels,stats,_=cv2.connectedComponentsWithStats(ink,8)
    mask=np.zeros_like(ink)
    target=np.zeros_like(ink,dtype=bool)
    target[max(0,int(y1)-top):min(gray.shape[0],int(y2)-top+1),max(0,int(x1)-left):min(gray.shape[1],int(x2)-left+1)]=True
    for i in range(1,count):
        area=stats[i,cv2.CC_STAT_AREA]
        if area<2:continue
        cw,ch=stats[i,cv2.CC_STAT_WIDTH],stats[i,cv2.CC_STAT_HEIGHT]
        if focus_bbox is not None:
            _,fy1,_,fy2=focus_bbox
            cy=top+stats[i,cv2.CC_STAT_TOP]+ch/2
            if cy<fy1-(fy2-fy1)*.2 or cy>fy2+(fy2-fy1)*.2:continue
        if (cw>(x2-x1)*.8 and ch<max(3,(y2-y1)*.06) or
                ch>(y2-y1)*.8 and cw<max(3,(x2-x1)*.02)):
            continue
        component=labels==i
        if (component & target).sum()/area>=.45:mask[component]=255
    points=cv2.findNonZero(mask)
    if points is None:return image.crop((round(x1),round(y1),round(x2),round(y2))),list(bbox)
    x,y,w,h=cv2.boundingRect(points)
    crop=255-mask[y:y+h,x:x+w]
    crop=cv2.copyMakeBorder(crop,8,8,8,8,cv2.BORDER_CONSTANT,value=255)
    return Image.fromarray(crop).convert('RGB'),[left+x,top+y,left+x+w,top+y+h]
