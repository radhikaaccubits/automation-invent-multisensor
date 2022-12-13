# ---------------------------------------------------------------------
# Project "Track 3D-Objects Over Time"
# Copyright (C) 2020, Dr. Antje Muntzinger / Dr. Andreas Haja.
#
# Purpose of this file : Detect 3D objects in lidar point clouds using deep learning
#
# You should have received a copy of the Udacity license together with this program.
#
# https://www.udacity.com/course/self-driving-car-engineer-nanodegree--nd013
# ----------------------------------------------------------------------
#

# general package imports
import numpy as np
import torch
from easydict import EasyDict as edict

# add project directory to python path to enable relative imports
import os
import sys

from tools.objdet_models.resnet.utils.torch_utils import _sigmoid

PACKAGE_PARENT = '..'
SCRIPT_DIR = os.path.dirname(os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(__file__))))
sys.path.append(os.path.normpath(os.path.join(SCRIPT_DIR, PACKAGE_PARENT)))

# model-related
from tools.objdet_models.resnet.models import fpn_resnet
from tools.objdet_models.resnet.utils.evaluation_utils import decode, post_processing

from tools.objdet_models.darknet.models.darknet2pytorch import Darknet as darknet
from tools.objdet_models.darknet.utils.evaluation_utils import post_processing_v2


def load_configs_model(model_name='darknet', configs=None):
    """"
    Load model-related parameters into an edict
    Ref https://github.com/maudzung/SFA3D/blob/master/sfa/test.py

    Parameters:
    model_name (string): name of the model to load
    configs (edict): dictionary containing model-related parameters

    Returns:
    configs (edict): dictionary with updated parameters configured
    """

    # init config file, if none has been passed
    if configs == None:
        configs = edict()

    # get parent directory of this file to enable relative paths
    curr_path = os.path.dirname(os.path.realpath(__file__))
    parent_path = configs.model_path = os.path.abspath(os.path.join(curr_path, os.pardir))

    # set parameters according to model type
    if model_name == "darknet":
        configs.model_path = os.path.join(parent_path, 'tools', 'objdet_models', 'darknet')
        configs.pretrained_filename = os.path.join(configs.model_path, 'pretrained', 'complex_yolov4_mse_loss.pth')
        configs.arch = 'darknet'
        configs.batch_size = 4
        configs.cfgfile = os.path.join(configs.model_path, 'config', 'complex_yolov4.cfg')
        configs.conf_thresh = 0.5
        configs.distributed = False
        configs.img_size = 608
        configs.nms_thresh = 0.4
        configs.num_samples = None
        configs.num_workers = 4
        configs.pin_memory = True
        configs.use_giou_loss = False

    elif model_name == 'fpn_resnet':
        configs.model_path = os.path.join(parent_path, 'tools', 'objdet_models', 'resnet')
        configs.pretrained_filename = configs.pretrained_path \
            = os.path.join(configs.model_path, 'pretrained', 'fpn_resnet_18_epoch_300.pth')
        configs.arch = 'fpn_resnet'
        configs.batch_size = 4
        configs.conf_thresh = 0.5
        configs.distributed = False
        configs.num_samples = None
        configs.num_workers = 1
        configs.pin_memory = True

        configs.num_layers = 18  # https://arxiv.org/pdf/2001.03343.pdf

        configs.saved_fn = 'fpn_resnet'
        configs.k = 50
        configs.peak_thresh = 0.2
        configs.save_test_output = False
        configs.output_format = 'image'
        configs.output_video_fn = 'out_fpn_resnet'
        configs.output_width = 608
        configs.distributed = False
        configs.input_size = (608, 608)
        configs.hm_size = (152, 152)
        configs.down_ratio = 4
        configs.max_objects = 50
        configs.imagenet_pretrained = False
        configs.head_conv = 64
        configs.num_classes = 3
        configs.num_center_offset = 2
        configs.num_z = 1
        configs.num_dim = 3
        configs.num_direction = 2  # sin, cos
        configs.heads = {'hm_cen': configs.num_classes, 'cen_offset': configs.num_center_offset,
                         'direction': configs.num_direction, 'z_coor': configs.num_z, 'dim': configs.num_dim}
        configs.num_input_features = 4

    else:
        raise ValueError("Error: Invalid model name")

    configs.min_iou = 0.5

    # GPU vs. CPU
    configs.no_cuda = True  # if true, cuda is not used
    configs.gpu_idx = 0  # GPU index to use.
    configs.device = torch.device('cpu' if configs.no_cuda else 'cuda:{}'.format(configs.gpu_idx))

    return configs


def load_configs(model_name='fpn_resnet', configs=None):
    """"
    Load all object-detection parameters into an edict

    Parameters:
    model_name (string): name of the model to load
    configs (edict): dictionary containing object and model-related parameters

    Returns:
    configs (edict): dictionary with updated parameters configured
    """

    # init config file, if none has been passed
    if configs == None:
        configs = edict()

    # birds-eye view (bev) parameters
    configs.lim_x = [0, 50]  # detection range in m
    configs.lim_y = [-25, 25]
    configs.lim_z = [-1, 3]
    configs.lim_r = [0, 1.0]  # reflected lidar intensity
    configs.bev_width = 608  # pixel resolution of bev image
    configs.bev_height = 608

    # add model-dependent parameters
    configs = load_configs_model(model_name, configs)

    # visualization parameters
    configs.output_width = 608  # width of result image (height may vary)
    configs.obj_colors = [[0, 255, 255], [0, 0, 255], [255, 0, 0]]  # 'Pedestrian': 0, 'Car': 1, 'Cyclist': 2

    return configs


def create_model(configs):
    """"
    Create model according to selected model type

    Parameters:
    configs (edict): dictionary containing object and model-related parameters

    Returns:
    model (): pytorch version of darknet or resnet
    """

    # check for availability of model file
    assert os.path.isfile(configs.pretrained_filename), "No file at {}".format(configs.pretrained_filename)

    # create model depending on architecture name
    if (configs.arch == 'darknet') and (configs.cfgfile is not None):
        print('using darknet')
        model = darknet(cfgfile=configs.cfgfile, use_giou_loss=configs.use_giou_loss)

    elif 'fpn_resnet' in configs.arch:
        print('using ResNet architecture with feature pyramid')
        model = fpn_resnet.get_pose_net(num_layers=configs.num_layers, heads=configs.heads,
                                        head_conv=configs.head_conv, imagenet_pretrained=configs.imagenet_pretrained)

    else:
        assert False, 'Undefined model backbone'

    # load model weights
    model.load_state_dict(torch.load(configs.pretrained_filename, map_location='cpu'))
    print('Loaded weights from {}\n'.format(configs.pretrained_filename))

    # set model to evaluation state
    configs.device = torch.device('cpu' if configs.no_cuda else 'cuda:{}'.format(configs.gpu_idx))
    model = model.to(device=configs.device)  # load model to either cpu or gpu
    model.eval()

    return model


def detect_objects(input_bev_maps, model, configs):
    """"
    Detect trained objects in birds-eye view and converts bounding boxes from BEV into vehicle space

    Parameters:
    input_bev_maps (tensor): bird eye view map of point cloud to feed to the model
    model (): pytorch version of darknet or resnet
    configs (edict): dictionary containing object and model-related parameters

    Returns:
    objects (list): detected bounding boxes in image coordinates [id, x, y, z, height, width, length, yaw]

    """

    ##################
    # Decode model output and perform post-processing
    ##################

    # deactivate autograd engine during test to reduce memory usage and speed up computations
    with torch.no_grad():

        # perform inference
        outputs = model(input_bev_maps)

        # decode model output into target object format
        if 'darknet' in configs.arch:

            # perform post-processing
            output_post = post_processing_v2(outputs, conf_thresh=configs.conf_thresh, nms_thresh=configs.nms_thresh)
            detections = []
            for sample_i in range(len(output_post)):
                if output_post[sample_i] is None:
                    continue
                detection = output_post[sample_i]
                for obj in detection:
                    x, y, w, l, im, re, _, _, _ = obj
                    yaw = np.arctan2(im, re)
                    detections.append([1, x, y, 0.0, 1.50, w, l, yaw])

        elif 'fpn_resnet' in configs.arch:
            # decode output and perform post-processing

            outputs['hm_cen'] = _sigmoid(outputs['hm_cen'])
            outputs['cen_offset'] = _sigmoid(outputs['cen_offset'])
            detections = decode(outputs['hm_cen'], outputs['cen_offset'],
                                outputs['direction'], outputs['z_coor'], outputs['dim'], K=configs.k)
            detections = detections.cpu().numpy().astype(np.float32)
            detections = post_processing(detections, configs)
            detections = detections[0][1]

    # Extract 3d bounding boxes from model response
    objects = []

    # check whether there are any detections
    if len(detections) > 0:
        # loop over all detections
        for obj in detections:
            # convert from BEV into vehicle space using the limits for x, y and z set in the configs structure
            _, bev_x, bev_y, z, bbox_bev_height, bbox_bev_width, bbox_bev_length, yaw = obj

            img_x = bev_y / configs.bev_height * (configs.lim_x[1] - configs.lim_x[0])
            img_y = bev_x / configs.bev_width * (configs.lim_y[1] - configs.lim_y[0]) - (
                    configs.lim_y[1] - configs.lim_y[0]) / 2.0
            bbox_img_width = bbox_bev_width / configs.bev_width * (configs.lim_y[1] - configs.lim_y[0])
            bbox_img_length = bbox_bev_length / configs.bev_height * (configs.lim_x[1] - configs.lim_x[0])
            if (configs.lim_x[0] <= img_x <= configs.lim_x[1]
                    and configs.lim_y[0] <= img_y <= configs.lim_y[1]
                    and configs.lim_z[0] <= z <= configs.lim_z[1]):
                # append the current object to the 'objects' array
                objects.append([1, img_x, img_y, z, bbox_bev_height, bbox_img_width, bbox_img_length, yaw])

    return objects
