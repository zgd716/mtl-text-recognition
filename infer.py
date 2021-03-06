# coding=utf-8

import os
import sys
import string
from PIL import Image
import argparse
import torch
from torch.autograd import Variable
import torch.backends.cudnn as cudnn
import torch.utils.data
import torchvision.transforms as transforms
from utils.utils import CTCLabelConverter, AttnLabelConverter
from utils.dataset import RawDataset, AlignCollate
from config import ConfigOpt
from model import Model
import logging
logging.basicConfig(
    format='[%(asctime)s] [%(filename)s]:[line:%(lineno)d] [%(levelname)s] %(message)s', level=logging.INFO)


class InferResizeNormalize(object):

    def __init__(self, size, interpolation=Image.BILINEAR):
        self.size = size
        self.interpolation = interpolation
        self.toTensor = transforms.ToTensor()

    def __call__(self, img):
        img = img.resize(self.size, self.interpolation)
        img = self.toTensor(img)
        img.sub_(0.5).div_(0.5)
        return img


class OcrRec:
    def __init__(self, opt=None):
        self.max_length = 25
        self.opt = ConfigOpt()
        if opt:
            self.opt = opt
        self.batch_size = 1
        self.model = None
        self.converter = None
        self.load_model()

    def load_model(self):
        if 'CTC' in self.opt.Prediction:
            self.converter = CTCLabelConverter(self.opt.character)
        else:
            self.converter = AttnLabelConverter(self.opt.character)
        self.opt.num_class = len(self.converter.character)
        if self.opt.rgb:
            self.opt.input_channel = 3
        self.model = Model(self.opt)
        print('model input parameters', self.opt.imgH, self.opt.imgW, self.opt.num_fiducial, self.opt.input_channel,
              self.opt.output_channel, self.opt.hidden_size, self.opt.num_class, self.opt.batch_max_length,
              self.opt.Transformation,  self.opt.FeatureExtraction, self.opt.SequenceModeling, self.opt.Prediction)
        self.model = torch.nn.DataParallel(self.model)
        if torch.cuda.is_available():
            self.model = self.model.cuda()
        # load model
        print('loading pretrained model from %s' % self.opt.saved_model)
        if torch.cuda.is_available():
            self.model.load_state_dict(torch.load(self.opt.saved_model))
        else:
            self.model.load_state_dict(torch.load(self.opt.saved_model, map_location="cpu"))
        self.model.eval()

    def text_rec(self, img):
        """
        resize PIL image to fixed height, keep width/height ratio
        do inference
        :param img:
        :return:
        """
        if isinstance(img, str) and os.path.isfile(img):
            img = Image.open(img)
            img = img.convert('L')
            import PIL.ImageOps
            # img = PIL.ImageOps.invert(img)
        if not img.mode == 'L':
            img = img.convert('L')
        ratio = self.opt.imgH / img.size[1]
        target_w = int(img.size[0] * ratio)
        transformer = InferResizeNormalize((target_w, self.opt.imgH))
        # image_tensors = [transformer(img)]
        # image_tensors = torch.cat([t.unsqueeze(0) for t in image_tensors], 0)
        img = transformer(img)
        img = img.view(1, *img.size())
        img = Variable(img)
        with torch.no_grad():
            if torch.cuda.is_available():
                img = img.cuda()
                length_for_pred = torch.cuda.IntTensor([self.opt.batch_max_length] * self.batch_size)
                text_for_pred = torch.cuda.LongTensor(self.batch_size, self.opt.batch_max_length + 1).fill_(0)
            else:
                length_for_pred = torch.IntTensor([self.opt.batch_max_length] * self.batch_size)
                text_for_pred = torch.LongTensor(self.batch_size, self.opt.batch_max_length + 1).fill_(0)
            if 'CTC' in self.opt.Prediction:
                preds = self.model(img, text_for_pred).softmax(2)
                # Select max probabilty (greedy decoding) then decode index to character
                preds_size = torch.IntTensor([preds.size(1)] * self.batch_size)
                preds_prob_vals, preds_index = preds.permute(1, 0, 2).max(2)
                # preds_prob_vals = preds_prob_vals.transpose(1, 0).contiguous().view(-1)
                preds_index = preds_index.transpose(1, 0).contiguous().view(-1)
                # print(preds_index)
                # print(preds_prob_vals)
                preds_str = self.converter.decode(preds_index.data, preds_size.data)
            else:
                preds = self.model(img, text_for_pred, is_train=False)
                # select max probabilty (greedy decoding) then decode index to character
                _, preds_index = preds.max(2)
                preds_str = self.converter.decode(preds_index, length_for_pred)
                preds_str = [pred[:pred.find('[s]')] for pred in preds_str]
            # print("pred:", preds_str[0])
        return preds_str[0]


if __name__ == '__main__':
    # cudnn.benchmark = True
    # cudnn.deterministic = True
    # opt.num_gpu = torch.cuda.device_count()
    opt = ConfigOpt()
    ocr_rec = OcrRec(opt=opt)
    image_path = sys.argv[1]
    if os.path.isfile(image_path):
        res_text = ocr_rec.text_rec(image_path)
        print(f"{image_path.split(os.path.sep)[-1]}\t{res_text}")
    elif os.path.isdir(image_path):
        image_list = os.listdir(image_path)
        for image_file in image_list:
            suffix = image_file.split('.')[-1]
            if suffix not in ('jpg', 'jpeg', 'png'):
                continue
            img_path = os.path.join(image_path, image_file)
            if not os.path.isfile(img_path):
                print(f"not file {img_path}")
                continue
            res_text = ocr_rec.text_rec(img_path)
            print(f"{image_file}\t{res_text}")




