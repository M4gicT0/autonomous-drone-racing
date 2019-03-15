#! /usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fenc=utf-8
#
# Copyright © 2019 transpalette <transpalette@arch-cactus>
#
# Distributed under terms of the MIT license.

"""
DatasetFactory

Generates a given number of images by projecting a given model in random
positions, onto randomly selected background images from the given dataset.
"""

import multiprocessing.dummy as mp
import numpy as np
import argparse
import cv2
import sys
import os

from pyrr import Vector3
from PIL import Image, ImageDraw
from scene_renderer import SceneRenderer
from dataset import Dataset, AnnotatedImage, SyntheticAnnotations


'''
    ----- TODO -----

[x] Thread it!
[x] Random positioning of the gate
[x] Boundaries definition for the gate (relative to the mesh's size)
[x] Compute the center of the gate
[x] Compute the presence of the gate in the image frame
[x] Convert world coordinates to image coordinates
[?] Compute the distance to the gate
[x] Perspective projection for visualization
[x] Camera calibration (use the correct parameters)
[x] Project on transparent background
[x] Overlay with background image
[x] Model the camera distortion
[ ] Add background gates
[ ] Save annotations
[ ] Apply the distortion to the OpenGL projection
[ ] Histogram equalization of both images (hue, saturation, luminence ?...)
[ ] Motion blur <-
[x] Anti alisasing
[ ] Ship it!

'''


class DatasetFactory:
    def __init__(self, args):
        self.mesh_path = args.mesh
        self.nb_threads = args.threads
        self.count = args.nb_images
        self.blur_amount = args.blur_amount
        self.cam_param = args.camera_parameters
        self.verbose = args.verbose
        self.render_perspective = args.extra_verbose
        self.debug = args.debug
        if self.render_perspective:
            self.verbose = True
        self.background_dataset = Dataset(args.dataset, args.debug)
        if not self.background_dataset.load(self.count, args.annotations):
            print("[!] Could not load dataset!")
            sys.exit(1)
        self.generated_dataset = Dataset(args.destination)
        self.base_width, self.base_height = self.background_dataset.get_image_size()
        self.target_width, self.target_height = [int(x) for x in args.resolution.split('x')]
        self.max_blur_amount = 1500

    def set_mesh_parameters(self, boundaries, gate_center):
        self.world_boundaries = boundaries
        self.gate_center = gate_center

    def set_max_blur_amount(self, val):
        self.max_blur_amount = val

    def run(self):
        print("[*] Generating dataset...")
        p = mp.Pool(self.nb_threads)
        p.map(self.generate, range(self.count))
        p.close()
        p.join()

        print("[*] Scaling to {}x{} resolution".format(self.target_width,
                                                       self.target_height))
        print("[*] Saving to {}".format(self.generated_dataset.path))
        self.generated_dataset.save(self.nb_threads)

    def generate(self, index):
        background = self.background_dataset.get()
        projector = SceneRenderer(self.mesh_path, self.base_width,
                                   self.base_height, self.world_boundaries,
                                   self.gate_center, self.cam_param,
                                   background.annotations,
                                  self.render_perspective, self.debug)
        projection, annotations = projector.generate()
        projection_blurred = self.apply_motion_blur(projection,
                                                    amount=self.get_blur_amount(background.image()))
        output = self.combine(projection_blurred, background.image())
        gate_center = self.scale_coordinates(
            annotations['gate_center_img_frame'], output.size)
        gate_visible = (gate_center[0] >=0 and gate_center[0] <=
                        output.size[0]) and (gate_center[1] >= 0 and
                                             gate_center[1] <= output.size[1])
        if self.verbose:
            self.draw_gate_center(output, gate_center)
            self.draw_image_annotations(output, annotations)

        self.generated_dataset.put(
            AnnotatedImage(output, index, SyntheticAnnotations(gate_center,
                                                               annotations['gate_rotation'],
                                                               gate_visible))
        )

    # Scale to target width/height
    def scale_coordinates(self, coordinates, target_coordinates):
        coordinates[0] = (coordinates[0] * target_coordinates[0]) / self.base_width
        coordinates[1] = (coordinates[1] * target_coordinates[1]) / self.base_height

        return coordinates

    # NB: Thumbnail() only scales down!!
    def combine(self, projection: Image, background: Image):
        background = background.convert('RGBA')
        projection.thumbnail((self.base_width, self.base_height), Image.ANTIALIAS)
        output = Image.alpha_composite(background, projection)
        output.thumbnail((self.target_width, self.target_height), Image.ANTIALIAS)

        return output

    def get_blur_amount(self, img: Image):
        gray_scale = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        variance_of_laplacian = cv2.Laplacian(gray_scale, cv2.CV_64F).var()
        blur_amount = variance_of_laplacian / self.max_blur_amount
        if blur_amount > 1:
            blur_amount = 1
        blur_amount = 1 - blur_amount
        print("blur ammount: {}".format(blur_amount))

        return blur_amount

    def apply_motion_blur(self, img: Image, amount=1):
        size = int(15 * amount)
        if size <= 0:
            size = 2
        kernel = np.zeros((size, size))
        kernel[int((size)/2), :] = np.ones(size)
        kernel /= size
        cv_img = np.array(img)

        return Image.fromarray(cv2.filter2D(cv_img, -1, kernel))

    def draw_gate_center(self, img, coordinates, color=(0, 255, 0, 255)):
        gate_draw = ImageDraw.Draw(img)
        gate_draw.line((coordinates[0] - 10, coordinates[1], coordinates[0] + 10,
                   coordinates[1]), fill=color)
        gate_draw.line((coordinates[0], coordinates[1] - 10, coordinates[0],
                   coordinates[1] + 10), fill=color)

    def draw_image_annotations(self, img, annotations, color=(0, 255, 0, 255)):
        text = "gate_center_image_frame: {}\ngate_position: {}\ngate_rotation: {}\ndrone_pose: {}\ndrone_orientation:{}".format(
            annotations['gate_center_img_frame'], annotations['gate_position'],
                annotations['gate_rotation'], annotations['drone_pose'],
                annotations['drone_orientation'])
        text_draw = ImageDraw.Draw(img)
        text_draw.text((0, 0), text, color)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Generate a hybrid synthetic dataset of projections of a \
        given 3D model, in random positions and orientations, onto randomly \
        selected background images from a given dataset.')
    parser.add_argument('mesh', help='the 3D mesh to project', type=str)
    parser.add_argument('dataset', help='the path to the background images \
                        dataset, with height, roll, pitch and yaw annotations',
                       type=str)
    parser.add_argument('annotations', help='the path to the CSV annotations\
                        file', type=str)
    parser.add_argument('destination', metavar='dest', help='the path\
                        to the destination folder for the generated dataset',
                        type=str)
    parser.add_argument('--count', dest='nb_images', default=5, type=int,
                        help='the number of images to be generated')
    parser.add_argument('--blur-amount', dest='blur_amount', default=0.3,
                        type=float, help='the percentage of motion blur to be \
                        added')
    parser.add_argument('--res', dest='resolution', default='640x480',
                        type=str, help='the desired resolution')
    parser.add_argument('-t', dest='threads', default=4, type=int,
                        help='the number of threads to use')
    parser.add_argument('--camera', dest='camera_parameters', type=str,
                        help='the path to the camera parameters YAML file',
                        required=True)
    parser.add_argument('-v', dest='verbose', help='verbose output',
                        action='store_true', default=False)
    parser.add_argument('-vv', dest='extra_verbose', help='extra verbose\
                        output (render the perspective grid)',
                        action='store_true', default=False)
    parser.add_argument('-d', dest='debug', action='store_true',
                        default=False, help='use a fixed seed')

    datasetFactory = DatasetFactory(parser.parse_args())
    datasetFactory.set_mesh_parameters(
        {'x': 12, 'y': 12}, # Real world boundaries in meters (relative to the mesh's scale)
        Vector3([0.0, 0.0, 2.3]) # Figure this out in Blender
    )
    datasetFactory.set_max_blur_amount(600) # Play with this value
    datasetFactory.run()
