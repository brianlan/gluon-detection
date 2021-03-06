import attr
import numpy as np
import torch

from logger import logger


@attr.s
class AnchorGenerator(object):
    @staticmethod
    def generate_anchors(fmap_shape, fmap_downsampled_rate, scales, ratios, anchor_stride=1):
        """

        :param fmap_shape:
        :param fmap_downsampled_rate: it's calculated by fmap_shape / im_shape, a concept similar to feature strides.
        :param scales:
        :param ratios:
        :param anchor_stride:
        :return:
        """
        # Get all combinations of scales and ratios
        scales, ratios = np.meshgrid(np.array(scales), np.array(ratios))
        scales = scales.flatten()
        ratios = ratios.flatten()

        # Enumerate heights and widths from scales and ratios
        heights = scales / np.sqrt(ratios)
        widths = scales * np.sqrt(ratios)

        # Enumerate shifts in feature space
        shifts_y = np.arange(0, fmap_shape[0], anchor_stride) / fmap_downsampled_rate
        shifts_x = np.arange(0, fmap_shape[1], anchor_stride) / fmap_downsampled_rate
        shifts_x, shifts_y = np.meshgrid(shifts_x, shifts_y)

        # Enumerate combinations of shifts, widths, and heights
        box_widths, box_centers_x = np.meshgrid(widths, shifts_x)
        box_heights, box_centers_y = np.meshgrid(heights, shifts_y)

        # Reshape to get a list of (y, x) and a list of (h, w)
        box_centers = np.stack(
            [box_centers_y, box_centers_x], axis=2).reshape([-1, 2])
        box_sizes = np.stack([box_heights, box_widths], axis=2).reshape([-1, 2])

        # Convert to corner coordinates (y1, x1, y2, x2)
        boxes = np.concatenate([box_centers - 0.5 * box_sizes,
                                box_centers + 0.5 * box_sizes], axis=1)
        return boxes


def calc_overlap(query_boxes, ref_boxes):
    n_queries, n_refs = query_boxes.shape[0], ref_boxes.shape[0]

    q_xmin = query_boxes[:, 0].view(-1, 1).repeat(1, n_refs)
    q_ymin = query_boxes[:, 1].view(-1, 1).repeat(1, n_refs)
    q_xmax = query_boxes[:, 2].view(-1, 1).repeat(1, n_refs)
    q_ymax = query_boxes[:, 3].view(-1, 1).repeat(1, n_refs)

    r_xmin = ref_boxes[:, 0].view(1, -1).repeat(n_queries, 1)
    r_ymin = ref_boxes[:, 1].view(1, -1).repeat(n_queries, 1)
    r_xmax = ref_boxes[:, 2].view(1, -1).repeat(n_queries, 1)
    r_ymax = ref_boxes[:, 3].view(1, -1).repeat(n_queries, 1)

    intersect_left = torch.max(q_xmin, r_xmin)
    intersect_top = torch.max(q_ymin, r_ymin)
    intersect_right = torch.min(q_xmax, r_xmax)
    intersect_bottom = torch.min(q_ymax, r_ymax)

    try:
        intersect_areas = torch.max(torch.Tensor([0]), intersect_right - intersect_left + 1) * \
                          torch.max(torch.Tensor([0]), intersect_bottom - intersect_top + 1)
    except RuntimeError as e:
        intersect_areas = torch.max(torch.Tensor([0]).cuda(), intersect_right - intersect_left + 1) * \
                          torch.max(torch.Tensor([0]).cuda(), intersect_bottom - intersect_top + 1)
        logger.debug('err_msg: {}'.format(e))

    query_areas = ((query_boxes[:, 2] - query_boxes[:, 0] + 1) * (query_boxes[:, 3] - query_boxes[:, 1] + 1)) \
        .view(-1, 1) \
        .repeat(1, n_refs)
    ref_areas = ((ref_boxes[:, 2] - ref_boxes[:, 0] + 1) * (ref_boxes[:, 3] - ref_boxes[:, 1] + 1)) \
        .view(1, -1) \
        .repeat(n_queries, 1)

    overlaps = intersect_areas / (query_areas + ref_areas - intersect_areas)
    return overlaps


def calc_anchor_match(anchors, gt_boxes, fmap_downsampled_rate, match_thresh_hi=.7, match_thresh_lo=.3):
    # TODO: handle the case where gt_boxes is empty
    gt_boxes = gt_boxes * fmap_downsampled_rate
    overlaps = calc_overlap(anchors, gt_boxes)
    max_ious_with_gt, assigned_gt_idx = overlaps.max(dim=1)
    assigned_gt_boxes = gt_boxes[assigned_gt_idx]

    idx_closest_to_gt = overlaps.argmax(dim=0)
    anchor_idx = torch.arange(len(anchors), dtype=torch.long)
    match = torch.ones(len(anchors)) * -1
    if torch.cuda.is_available():
        match = match.cuda()
        anchor_idx = anchor_idx.cuda()
    match[idx_closest_to_gt] = 1
    match[max_ious_with_gt > match_thresh_hi] = 1
    match[max_ious_with_gt < match_thresh_lo] = 0
    # TODO: If multiple anchors have the same IoU match all of them
    pos_idx = anchor_idx[match == 1]
    neg_idx = anchor_idx[match == 0]

    return pos_idx, neg_idx, assigned_gt_boxes


def calc_delta(boxes, ref_boxes, delta_std_dev):
    ref_w = ref_boxes[:, 2] - ref_boxes[:, 0]
    ref_h = ref_boxes[:, 3] - ref_boxes[:, 1]
    ref_center_x = ref_boxes[:, 0] + 0.5 * ref_w
    ref_center_y = ref_boxes[:, 1] + 0.5 * ref_h

    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]
    center_x = boxes[:, 0] + 0.5 * w
    center_y = boxes[:, 1] + 0.5 * h

    delta = torch.cat(((ref_center_x - center_x) / w,
                       (ref_center_y - center_y) / h,
                       torch.log(ref_w / w),
                       torch.log(ref_h / h))).view(*boxes.shape) / delta_std_dev
    return delta


def apply_delta(base_boxes, delta):
    pass
