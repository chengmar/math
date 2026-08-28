#!/usr/bin/env python3
"""Blind, reproducible solution pipeline for CUMCM 2008 A.

The script reads only the image mechanically extracted from the supplied Word
document.  It detects the five circular targets, fits their projected conics,
compares a simple affine/centroid baseline with two projective candidates, and
generates all numerical results and figures used by the paper.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy
import yaml
from PIL import Image
from scipy import ndimage
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


DEFAULT_SEED = 2008
LABELS = ("A", "B", "C", "D", "E")
WORLD_CENTERS = {
    "A": np.array([0.0, 0.0]),
    "B": np.array([30.0, 0.0]),
    "C": np.array([100.0, 0.0]),
    "D": np.array([100.0, 100.0]),
    "E": np.array([0.0, 100.0]),
}
RADIUS_MM = 12.0
FOCAL_PX = 1577.0
PIXELS_PER_MM = 3.78


@dataclass
class Component:
    component_id: int
    area: int
    bbox: tuple[int, int, int, int]
    centroid: np.ndarray


@dataclass
class Observation:
    threshold: float
    label_image: np.ndarray
    components: list[Component]
    component_for_label: dict[str, Component]
    boundaries: dict[str, np.ndarray]
    ellipse_params: dict[str, np.ndarray]
    ellipse_conics: dict[str, np.ndarray]
    assignment_best_rmse: float
    assignment_second_rmse: float
    assignment_centroid_best_rmse: float
    assignment_candidates_checked: int


def json_default(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value)!r}")


def write_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def status(condition: bool) -> str:
    return "pass" if bool(condition) else "fail"


def dominant_levels(image: np.ndarray) -> tuple[int, int, float]:
    hist = np.bincount(image.ravel(), minlength=256)
    dark = int(np.argmax(hist[:128]))
    bright = int(128 + np.argmax(hist[128:]))
    return dark, bright, 0.5 * (dark + bright)


def find_components(image: np.ndarray, threshold: float) -> tuple[np.ndarray, list[Component]]:
    binary = image < threshold
    labels, _ = ndimage.label(binary, structure=np.ones((3, 3), dtype=int))
    objects = ndimage.find_objects(labels)
    components: list[Component] = []
    for component_id, slices in enumerate(objects, start=1):
        if slices is None:
            continue
        local = labels[slices] == component_id
        area = int(local.sum())
        if area < 500:
            continue
        yy, xx = np.nonzero(labels == component_id)
        y_slice, x_slice = slices
        components.append(
            Component(
                component_id=component_id,
                area=area,
                bbox=(x_slice.start, y_slice.start, x_slice.stop - 1, y_slice.stop - 1),
                centroid=np.array([xx.mean(), yy.mean()], dtype=float),
            )
        )
    return labels, components


def normalize_points(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    center = points.mean(axis=0)
    mean_distance = np.linalg.norm(points - center, axis=1).mean()
    if mean_distance <= np.finfo(float).eps:
        raise ValueError("Degenerate point set")
    scale = math.sqrt(2.0) / mean_distance
    transform = np.array(
        [[scale, 0.0, -scale * center[0]], [0.0, scale, -scale * center[1]], [0.0, 0.0, 1.0]]
    )
    homogeneous = np.column_stack((points, np.ones(len(points))))
    normalized = (transform @ homogeneous.T).T
    return normalized[:, :2], transform


def dlt_homography(source: np.ndarray, destination: np.ndarray) -> np.ndarray:
    if len(source) < 4 or len(destination) != len(source):
        raise ValueError("At least four paired points are required")
    if np.linalg.matrix_rank(source - np.mean(source, axis=0)) < 2:
        raise ValueError("Source control points are collinear or coincident")
    if np.linalg.matrix_rank(destination - np.mean(destination, axis=0)) < 2:
        raise ValueError("Destination control points are collinear or coincident")
    source_n, source_t = normalize_points(source)
    destination_n, destination_t = normalize_points(destination)
    rows = []
    for (x, y), (u, v) in zip(source_n, destination_n, strict=True):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.asarray(rows, dtype=float))
    h_normalized = vt[-1].reshape(3, 3)
    h = np.linalg.inv(destination_t) @ h_normalized @ source_t
    scale = h[2, 2]
    if abs(scale) < 1e-12:
        scale = np.linalg.norm(h)
    return h / scale


def project_points(h: np.ndarray, points: np.ndarray) -> np.ndarray:
    homogeneous = np.column_stack((points, np.ones(len(points))))
    projected = (h @ homogeneous.T).T
    return projected[:, :2] / projected[:, 2:3]


def assignment_candidates(components: list[Component]) -> list[tuple[float, tuple[int, ...]]]:
    if len(components) != 5:
        raise ValueError(f"Expected five target components, found {len(components)}")
    world = np.vstack([WORLD_CENTERS[label] for label in LABELS])
    observed = np.vstack([component.centroid for component in components])
    candidates = []
    for permutation in itertools.permutations(range(5)):
        destination = observed[list(permutation)]
        try:
            h = dlt_homography(world, destination)
            residual = project_points(h, world) - destination
            rmse = float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))
            if np.isfinite(rmse):
                candidates.append((rmse, permutation))
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            continue
    if len(candidates) < 2:
        raise RuntimeError("Target label assignment was not identifiable")
    candidates.sort(key=lambda item: item[0])
    return candidates


def crossing_coordinate(a: float, b: float, integer_coordinate: int, threshold: float) -> float:
    if b == a:
        return integer_coordinate + 0.5
    fraction = (threshold - a) / (b - a)
    return integer_coordinate + float(np.clip(fraction, 0.0, 1.0))


def subpixel_boundary(image: np.ndarray, component: Component, threshold: float) -> np.ndarray:
    height, width = image.shape
    x0, y0, x1, y1 = component.bbox
    x_start, x_stop = max(0, x0 - 3), min(width - 1, x1 + 3)
    y_start, y_stop = max(0, y0 - 3), min(height - 1, y1 + 3)
    points: list[tuple[float, float]] = []

    for y in range(y_start, y_stop + 1):
        values = image[y, x_start : x_stop + 1].astype(float)
        inside = values < threshold
        transitions = np.flatnonzero(inside[:-1] != inside[1:])
        for index in transitions:
            x_integer = x_start + int(index)
            x = crossing_coordinate(values[index], values[index + 1], x_integer, threshold)
            points.append((x, float(y)))

    for x in range(x_start, x_stop + 1):
        values = image[y_start : y_stop + 1, x].astype(float)
        inside = values < threshold
        transitions = np.flatnonzero(inside[:-1] != inside[1:])
        for index in transitions:
            y_integer = y_start + int(index)
            y = crossing_coordinate(values[index], values[index + 1], y_integer, threshold)
            points.append((float(x), y))

    boundary = np.asarray(points, dtype=float)
    if len(boundary) < 40:
        raise RuntimeError(f"Insufficient boundary points for component {component.component_id}")
    return boundary


def direct_conic_fit(points: np.ndarray) -> np.ndarray:
    normalized, transform = normalize_points(points)
    x, y = normalized.T
    design = np.column_stack((x * x, x * y, y * y, x, y, np.ones(len(points))))
    _, _, vt = np.linalg.svd(design, full_matrices=False)
    a, b, c, d, e, f = vt[-1]
    q_normalized = np.array([[a, b / 2.0, d / 2.0], [b / 2.0, c, e / 2.0], [d / 2.0, e / 2.0, f]])
    return transform.T @ q_normalized @ transform


def conic_to_ellipse(q: np.ndarray) -> np.ndarray:
    q = 0.5 * (q + q.T)
    quadratic = q[:2, :2]
    linear = q[:2, 2]
    center = -np.linalg.solve(quadratic, linear)
    center_h = np.array([center[0], center[1], 1.0])
    value_at_center = float(center_h @ q @ center_h)
    eigenvalues, eigenvectors = np.linalg.eigh(quadratic)
    axis_squared = -value_at_center / eigenvalues
    if np.any(axis_squared <= 0.0) or not np.all(np.isfinite(axis_squared)):
        raise ValueError("The fitted conic is not an ellipse")
    axes = np.sqrt(axis_squared)
    order = np.argsort(axes)[::-1]
    axes = axes[order]
    major_vector = eigenvectors[:, order[0]]
    angle = math.atan2(major_vector[1], major_vector[0])
    return np.array([center[0], center[1], math.log(axes[0]), math.log(axes[1]), angle])


def ellipse_to_conic(parameters: np.ndarray) -> np.ndarray:
    cx, cy, log_major, log_minor, angle = parameters
    major, minor = math.exp(log_major), math.exp(log_minor)
    cosine, sine = math.cos(angle), math.sin(angle)
    rotation = np.array([[cosine, -sine], [sine, cosine]])
    quadratic = rotation @ np.diag([1.0 / major**2, 1.0 / minor**2]) @ rotation.T
    center = np.array([cx, cy])
    q = np.zeros((3, 3), dtype=float)
    q[:2, :2] = quadratic
    q[:2, 2] = -quadratic @ center
    q[2, :2] = q[:2, 2]
    q[2, 2] = float(center @ quadratic @ center - 1.0)
    return q


def sampson_residual(q: np.ndarray, points: np.ndarray) -> np.ndarray:
    homogeneous = np.column_stack((points, np.ones(len(points))))
    q_points = (q @ homogeneous.T).T
    values = np.einsum("ij,ij->i", homogeneous, q_points)
    gradient_norm = 2.0 * np.linalg.norm(q_points[:, :2], axis=1)
    return values / np.maximum(gradient_norm, 1e-12)


def fit_ellipse(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    initial_q = direct_conic_fit(points)
    initial = conic_to_ellipse(initial_q)

    def residual(parameters: np.ndarray) -> np.ndarray:
        return sampson_residual(ellipse_to_conic(parameters), points)

    fit = least_squares(
        residual,
        initial,
        loss="soft_l1",
        f_scale=0.25,
        x_scale="jac",
        max_nfev=2000,
        ftol=1e-12,
        xtol=1e-12,
        gtol=1e-12,
    )
    if not fit.success:
        raise RuntimeError(f"Ellipse optimization failed: {fit.message}")
    return fit.x, ellipse_to_conic(fit.x)


def prepare_observation(image: np.ndarray, threshold: float) -> Observation:
    label_image, components = find_components(image, threshold)
    candidates = assignment_candidates(components)

    # Center correspondences alone leave a near-exact D/E swap ambiguity.  Resolve
    # it with the complete equal-radius circle boundaries under one homography.
    raw_boundaries: dict[int, np.ndarray] = {}
    raw_ellipse_params: dict[int, np.ndarray] = {}
    raw_ellipse_conics: dict[int, np.ndarray] = {}
    for component in components:
        points = subpixel_boundary(image, component, threshold)
        parameters, conic = fit_ellipse(points)
        raw_boundaries[component.component_id] = points
        raw_ellipse_params[component.component_id] = parameters
        raw_ellipse_conics[component.component_id] = conic

    centroid_best = candidates[0][0]
    plausible = [candidate for candidate in candidates if candidate[0] <= centroid_best + 5.0][:12]
    if len(plausible) < 2:
        plausible = candidates[:2]
    world = np.vstack([WORLD_CENTERS[label] for label in LABELS])
    joint_scores = []
    for centroid_rmse, permutation in plausible:
        local_mapping = {
            label: components[index] for label, index in zip(LABELS, permutation, strict=True)
        }
        local_boundaries = {
            label: raw_boundaries[local_mapping[label].component_id] for label in LABELS
        }
        local_centers = np.vstack(
            [raw_ellipse_params[local_mapping[label].component_id][:2] for label in LABELS]
        )
        local_initial_h = dlt_homography(world, local_centers)
        try:
            local_h, _ = fit_homography(local_boundaries, local_initial_h)
            joint_rmse = edge_metrics(local_h, local_boundaries)["overall"]["rmse_px"]
            joint_scores.append((joint_rmse, centroid_rmse, permutation))
        except (RuntimeError, ValueError, np.linalg.LinAlgError):
            continue
    if len(joint_scores) < 2:
        raise RuntimeError("Full-boundary target label assignment was not identifiable")
    joint_scores.sort(key=lambda item: item[0])
    best_rmse, _, best_permutation = joint_scores[0]
    second_rmse = joint_scores[1][0]
    mapping = {
        label: components[index] for label, index in zip(LABELS, best_permutation, strict=True)
    }
    boundaries = {label: raw_boundaries[mapping[label].component_id] for label in LABELS}
    ellipse_params = {label: raw_ellipse_params[mapping[label].component_id] for label in LABELS}
    ellipse_conics = {label: raw_ellipse_conics[mapping[label].component_id] for label in LABELS}
    return Observation(
        threshold=threshold,
        label_image=label_image,
        components=components,
        component_for_label=mapping,
        boundaries=boundaries,
        ellipse_params=ellipse_params,
        ellipse_conics=ellipse_conics,
        assignment_best_rmse=best_rmse,
        assignment_second_rmse=second_rmse,
        assignment_centroid_best_rmse=centroid_best,
        assignment_candidates_checked=len(joint_scores),
    )


def circle_conic(center: np.ndarray, radius: float = RADIUS_MM) -> np.ndarray:
    x, y = center
    return np.array(
        [[1.0, 0.0, -x], [0.0, 1.0, -y], [-x, -y, x * x + y * y - radius * radius]],
        dtype=float,
    )


def image_circle_conic(h: np.ndarray, label: str) -> np.ndarray:
    inverse = np.linalg.inv(h)
    return inverse.T @ circle_conic(WORLD_CENTERS[label]) @ inverse


def homography_to_parameters(h: np.ndarray) -> np.ndarray:
    h = h / h[2, 2]
    return np.array([h[0, 0], h[0, 1], h[0, 2], h[1, 0], h[1, 1], h[1, 2], h[2, 0], h[2, 1]])


def parameters_to_homography(parameters: np.ndarray) -> np.ndarray:
    return np.array(
        [
            [parameters[0], parameters[1], parameters[2]],
            [parameters[3], parameters[4], parameters[5]],
            [parameters[6], parameters[7], 1.0],
        ]
    )


def contour_residuals(h: np.ndarray, boundaries: dict[str, np.ndarray], include: Iterable[str]) -> np.ndarray:
    residuals = []
    try:
        for label in include:
            residuals.append(sampson_residual(image_circle_conic(h, label), boundaries[label]))
    except np.linalg.LinAlgError:
        return np.full(sum(len(boundaries[label]) for label in include), 1e6)
    return np.concatenate(residuals)


def fit_homography(
    boundaries: dict[str, np.ndarray], initial_h: np.ndarray, include: Iterable[str] = LABELS
) -> tuple[np.ndarray, object]:
    include = tuple(include)
    initial = homography_to_parameters(initial_h)

    def residual(parameters: np.ndarray) -> np.ndarray:
        return contour_residuals(parameters_to_homography(parameters), boundaries, include)

    fit = least_squares(
        residual,
        initial,
        loss="soft_l1",
        f_scale=0.35,
        x_scale="jac",
        max_nfev=2500,
        ftol=1e-11,
        xtol=1e-11,
        gtol=1e-11,
    )
    if not fit.success:
        raise RuntimeError(f"Homography optimization failed: {fit.message}")
    return parameters_to_homography(fit.x), fit


def decompose_homography(h: np.ndarray, intrinsic: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    normalized = np.linalg.inv(intrinsic) @ h
    scale = 2.0 / (np.linalg.norm(normalized[:, 0]) + np.linalg.norm(normalized[:, 1]))
    if scale * normalized[2, 2] < 0.0:
        scale = -scale
    r1 = scale * normalized[:, 0]
    r2 = scale * normalized[:, 1]
    translation = scale * normalized[:, 2]
    raw_rotation = np.column_stack((r1, r2, np.cross(r1, r2)))
    u, _, vt = np.linalg.svd(raw_rotation)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0.0:
        u[:, -1] *= -1.0
        rotation = u @ vt
    return rotation, translation


def pose_to_homography(parameters: np.ndarray, intrinsic: np.ndarray) -> np.ndarray:
    rotation = Rotation.from_rotvec(parameters[:3]).as_matrix()
    translation = parameters[3:]
    return intrinsic @ np.column_stack((rotation[:, 0], rotation[:, 1], translation))


def pose_depths(parameters: np.ndarray) -> np.ndarray:
    rotation = Rotation.from_rotvec(parameters[:3]).as_matrix()
    translation = parameters[3:]
    samples = []
    for label in LABELS:
        center = WORLD_CENTERS[label]
        samples.append(center)
        for angle in (0.0, 0.5 * math.pi, math.pi, 1.5 * math.pi):
            samples.append(center + RADIUS_MM * np.array([math.cos(angle), math.sin(angle)]))
    points = np.asarray(samples)
    return points @ rotation[2, :2] + translation[2]


def fit_pose(
    boundaries: dict[str, np.ndarray],
    intrinsic: np.ndarray,
    initial_h: np.ndarray,
    include: Iterable[str] = LABELS,
) -> tuple[np.ndarray, np.ndarray, object]:
    include = tuple(include)
    initial_rotation, initial_translation = decompose_homography(initial_h, intrinsic)
    initial = np.concatenate((Rotation.from_matrix(initial_rotation).as_rotvec(), initial_translation))

    def residual(parameters: np.ndarray) -> np.ndarray:
        h = pose_to_homography(parameters, intrinsic)
        contour = contour_residuals(h, boundaries, include)
        depth_penalty = 100.0 * np.minimum(pose_depths(parameters) - 1.0, 0.0)
        return np.concatenate((contour, depth_penalty))

    fit = least_squares(
        residual,
        initial,
        loss="soft_l1",
        f_scale=0.35,
        x_scale="jac",
        max_nfev=3000,
        ftol=1e-11,
        xtol=1e-11,
        gtol=1e-11,
    )
    if not fit.success:
        raise RuntimeError(f"Pose optimization failed: {fit.message}")
    h = pose_to_homography(fit.x, intrinsic)
    return h, fit.x, fit


def conic_center(q: np.ndarray) -> np.ndarray:
    return -np.linalg.solve(q[:2, :2], q[:2, 2])


def edge_metrics(h: np.ndarray, boundaries: dict[str, np.ndarray], include: Iterable[str] = LABELS) -> dict:
    per_target = {}
    all_residuals = []
    for label in include:
        residual = sampson_residual(image_circle_conic(h, label), boundaries[label])
        absolute = np.abs(residual)
        all_residuals.append(residual)
        per_target[label] = {
            "rmse_px": float(np.sqrt(np.mean(residual**2))),
            "median_abs_px": float(np.median(absolute)),
            "p95_abs_px": float(np.percentile(absolute, 95)),
            "max_abs_px": float(np.max(absolute)),
        }
    combined = np.concatenate(all_residuals)
    absolute = np.abs(combined)
    return {
        "overall": {
            "rmse_px": float(np.sqrt(np.mean(combined**2))),
            "median_abs_px": float(np.median(absolute)),
            "p95_abs_px": float(np.percentile(absolute, 95)),
            "max_abs_px": float(np.max(absolute)),
            "n_boundary_points": int(len(combined)),
        },
        "per_target": per_target,
    }


def conic_iou(q: np.ndarray, observed_mask: np.ndarray) -> float:
    yy, xx = np.indices(observed_mask.shape)
    homogeneous = np.stack((xx.ravel(), yy.ravel(), np.ones(xx.size)), axis=1)
    q_points = (q @ homogeneous.T).T
    values = np.einsum("ij,ij->i", homogeneous, q_points).reshape(observed_mask.shape)
    center = conic_center(q)
    center_h = np.array([center[0], center[1], 1.0])
    sign_at_center = float(center_h @ q @ center_h)
    predicted = values * sign_at_center >= 0.0
    intersection = np.logical_and(predicted, observed_mask).sum()
    union = np.logical_or(predicted, observed_mask).sum()
    return float(intersection / union)


def candidate_metrics(h: np.ndarray, observation: Observation) -> dict:
    edge = edge_metrics(h, observation.boundaries)
    centers = {}
    ious = {}
    for label in LABELS:
        predicted_q = image_circle_conic(h, label)
        predicted_ellipse_center = conic_center(predicted_q)
        observed_ellipse_center = observation.ellipse_params[label][:2]
        centers[label] = float(np.linalg.norm(predicted_ellipse_center - observed_ellipse_center))
        component_id = observation.component_for_label[label].component_id
        observed_mask = observation.label_image == component_id
        ious[label] = conic_iou(predicted_q, observed_mask)
    return {
        "edge": edge,
        "ellipse_center_rmse_px": float(np.sqrt(np.mean(np.square(list(centers.values()))))),
        "ellipse_center_error_px": centers,
        "silhouette_iou": ious,
        "mean_silhouette_iou": float(np.mean(list(ious.values()))),
    }


def fit_affine(source: np.ndarray, destination: np.ndarray) -> np.ndarray:
    design = np.column_stack((source, np.ones(len(source))))
    coefficients, _, _, _ = np.linalg.lstsq(design, destination, rcond=None)
    return coefficients


def affine_project(coefficients: np.ndarray, points: np.ndarray) -> np.ndarray:
    return np.column_stack((points, np.ones(len(points)))) @ coefficients


def baseline_validation(observation: Observation) -> dict:
    world = np.vstack([WORLD_CENTERS[label] for label in LABELS])
    centroids = np.vstack([observation.component_for_label[label].centroid for label in LABELS])
    coefficients = fit_affine(world, centroids)
    fitted = affine_project(coefficients, world)
    in_sample = np.linalg.norm(fitted - centroids, axis=1)
    leave_one_out = {}
    for held_index, held_label in enumerate(LABELS):
        keep = np.arange(len(LABELS)) != held_index
        local = fit_affine(world[keep], centroids[keep])
        predicted = affine_project(local, world[[held_index]])[0]
        leave_one_out[held_label] = {
            "predicted_u_px": float(predicted[0]),
            "predicted_v_px": float(predicted[1]),
            "center_error_px": float(np.linalg.norm(predicted - centroids[held_index])),
        }
    errors = np.array([leave_one_out[label]["center_error_px"] for label in LABELS])
    return {
        "definition": "thresholded-component centroid with a global affine map",
        "affine_coefficients": coefficients,
        "in_sample_center_rmse_px": float(np.sqrt(np.mean(in_sample**2))),
        "leave_one_circle_out": leave_one_out,
        "leave_one_circle_out_mean_center_error_px": float(errors.mean()),
        "leave_one_circle_out_max_center_error_px": float(errors.max()),
    }


def leave_one_circle_out(
    observation: Observation,
    intrinsic: np.ndarray,
    free_h: np.ndarray,
    pose_h: np.ndarray,
) -> dict:
    result = {"planar_homography": {}, "calibrated_pose": {}}
    for held_label in LABELS:
        training = tuple(label for label in LABELS if label != held_label)
        held_boundary = {held_label: observation.boundaries[held_label]}

        local_free_h, _ = fit_homography(observation.boundaries, free_h, training)
        free_edge = edge_metrics(local_free_h, observation.boundaries, (held_label,))["per_target"][held_label]
        free_q = image_circle_conic(local_free_h, held_label)
        free_center_error = float(
            np.linalg.norm(conic_center(free_q) - observation.ellipse_params[held_label][:2])
        )
        component_id = observation.component_for_label[held_label].component_id
        free_iou = conic_iou(free_q, observation.label_image == component_id)
        result["planar_homography"][held_label] = {
            **free_edge,
            "ellipse_center_error_px": free_center_error,
            "silhouette_iou": free_iou,
        }

        local_pose_h, _, _ = fit_pose(observation.boundaries, intrinsic, pose_h, training)
        pose_edge = edge_metrics(local_pose_h, observation.boundaries, (held_label,))["per_target"][held_label]
        pose_q = image_circle_conic(local_pose_h, held_label)
        pose_center_error = float(
            np.linalg.norm(conic_center(pose_q) - observation.ellipse_params[held_label][:2])
        )
        pose_iou = conic_iou(pose_q, observation.label_image == component_id)
        result["calibrated_pose"][held_label] = {
            **pose_edge,
            "ellipse_center_error_px": pose_center_error,
            "silhouette_iou": pose_iou,
        }

    for model in ("planar_homography", "calibrated_pose"):
        rows = result[model]
        result[model]["summary"] = {
            "mean_edge_rmse_px": float(np.mean([rows[label]["rmse_px"] for label in LABELS])),
            "max_edge_rmse_px": float(np.max([rows[label]["rmse_px"] for label in LABELS])),
            "mean_ellipse_center_error_px": float(
                np.mean([rows[label]["ellipse_center_error_px"] for label in LABELS])
            ),
            "min_silhouette_iou": float(np.min([rows[label]["silhouette_iou"] for label in LABELS])),
        }
    return result


def ellipse_curve(q: np.ndarray, count: int = 400) -> np.ndarray:
    parameters = conic_to_ellipse(q)
    cx, cy, log_major, log_minor, angle = parameters
    major, minor = math.exp(log_major), math.exp(log_minor)
    theta = np.linspace(0.0, 2.0 * math.pi, count)
    base = np.column_stack((major * np.cos(theta), minor * np.sin(theta)))
    cosine, sine = math.cos(angle), math.sin(angle)
    rotation = np.array([[cosine, -sine], [sine, cosine]])
    return base @ rotation.T + np.array([cx, cy])


def main_centers(h: np.ndarray) -> dict[str, np.ndarray]:
    world = np.vstack([WORLD_CENTERS[label] for label in LABELS])
    projected = project_points(h, world)
    return {label: projected[index] for index, label in enumerate(LABELS)}


def threshold_sensitivity(
    image: np.ndarray,
    thresholds: list[float],
    intrinsic: np.ndarray,
    selected_model: str,
    initial_h: np.ndarray,
) -> tuple[list[dict], dict[str, np.ndarray]]:
    rows = []
    center_sets = {label: [] for label in LABELS}
    current_h = initial_h
    for threshold in thresholds:
        observation = prepare_observation(image, threshold)
        if selected_model == "planar_homography":
            fitted_h, _ = fit_homography(observation.boundaries, current_h)
        else:
            fitted_h, _, _ = fit_pose(observation.boundaries, intrinsic, current_h)
        current_h = fitted_h
        centers = main_centers(fitted_h)
        for label in LABELS:
            center_sets[label].append(centers[label])
            rows.append(
                {
                    "threshold": float(threshold),
                    "label": label,
                    "u_px": float(centers[label][0]),
                    "v_px": float(centers[label][1]),
                }
            )
    arrays = {label: np.vstack(values) for label, values in center_sets.items()}
    return rows, arrays


def monte_carlo_contours(
    observation: Observation,
    intrinsic: np.ndarray,
    selected_model: str,
    initial_h: np.ndarray,
    repetitions: int,
    sigma_px: float,
    seed: int,
) -> tuple[dict[str, np.ndarray], list[dict]]:
    rng = np.random.default_rng(seed)
    samples = {label: [] for label in LABELS}
    run_rows = []
    for run in range(repetitions):
        perturbed = {
            label: observation.boundaries[label]
            + rng.normal(0.0, sigma_px, size=observation.boundaries[label].shape)
            for label in LABELS
        }
        if selected_model == "planar_homography":
            fitted_h, _ = fit_homography(perturbed, initial_h)
        else:
            fitted_h, _, _ = fit_pose(perturbed, intrinsic, initial_h)
        centers = main_centers(fitted_h)
        for label in LABELS:
            samples[label].append(centers[label])
            run_rows.append(
                {
                    "run": run,
                    "label": label,
                    "u_px": float(centers[label][0]),
                    "v_px": float(centers[label][1]),
                }
            )
    return {label: np.vstack(values) for label, values in samples.items()}, run_rows


def save_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def plot_segmentation(image: np.ndarray, observation: Observation, figures_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10.24, 7.68), dpi=120)
    ax.imshow(image, cmap="gray", vmin=0, vmax=255)
    colors = plt.cm.tab10(np.linspace(0.0, 1.0, 5))
    for color, label in zip(colors, LABELS, strict=True):
        boundary = observation.boundaries[label]
        curve = ellipse_curve(observation.ellipse_conics[label])
        centroid = observation.component_for_label[label].centroid
        ax.scatter(boundary[::5, 0], boundary[::5, 1], s=3, color=color, alpha=0.45)
        ax.plot(curve[:, 0], curve[:, 1], color=color, linewidth=1.2)
        ax.plot(centroid[0], centroid[1], marker="+", color=color, markersize=10, markeredgewidth=1.5)
        ax.text(centroid[0] + 8, centroid[1] - 8, label, color=color, fontsize=12, weight="bold")
    ax.set_title(f"Segmentation and individual ellipse fits (threshold={observation.threshold:.1f})")
    ax.set_xlabel("u (pixel)")
    ax.set_ylabel("v (pixel, downward)")
    ax.set_xlim(150, 750)
    ax.set_ylim(620, 80)
    ax.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)


def plot_model_fit(
    image: np.ndarray, observation: Observation, h: np.ndarray, selected_model: str, figures_dir: Path
) -> None:
    centers = main_centers(h)
    fig, ax = plt.subplots(figsize=(10.24, 7.68), dpi=120)
    ax.imshow(image, cmap="gray", vmin=0, vmax=255)
    for label in LABELS:
        predicted_curve = ellipse_curve(image_circle_conic(h, label))
        ellipse_center = observation.ellipse_params[label][:2]
        corrected = centers[label]
        ax.plot(predicted_curve[:, 0], predicted_curve[:, 1], color="#e31a1c", linewidth=1.3)
        ax.plot(ellipse_center[0], ellipse_center[1], marker="x", color="#1f78b4", markersize=7)
        ax.plot(corrected[0], corrected[1], marker="+", color="#e31a1c", markersize=9, markeredgewidth=1.5)
        ax.annotate(
            "",
            xy=corrected,
            xytext=ellipse_center,
            arrowprops={"arrowstyle": "->", "color": "#33a02c", "lw": 1.0},
        )
        ax.text(corrected[0] + 7, corrected[1] - 7, label, color="#e31a1c", fontsize=11, weight="bold")
    ax.plot([], [], color="#e31a1c", label="joint-model conic / projected center (+)")
    ax.plot([], [], marker="x", linestyle="none", color="#1f78b4", label="individual ellipse center")
    ax.set_title(f"Joint fit and perspective center correction: {selected_model}")
    ax.set_xlabel("u (pixel)")
    ax.set_ylabel("v (pixel, downward)")
    ax.set_xlim(150, 750)
    ax.set_ylim(620, 80)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)


def plot_model_comparison(
    baseline: dict, free_metrics: dict, pose_metrics: dict, validation: dict, figures_dir: Path
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), dpi=140)
    models = ["Free homography", "Calibrated pose"]
    in_sample = [
        free_metrics["edge"]["overall"]["rmse_px"],
        pose_metrics["edge"]["overall"]["rmse_px"],
    ]
    holdout = [
        validation["planar_homography"]["summary"]["mean_edge_rmse_px"],
        validation["calibrated_pose"]["summary"]["mean_edge_rmse_px"],
    ]
    x = np.arange(2)
    width = 0.34
    axes[0].bar(x - width / 2, in_sample, width, label="in-sample")
    axes[0].bar(x + width / 2, holdout, width, label="leave-one-circle-out")
    axes[0].set_xticks(x, models, rotation=12)
    axes[0].set_ylabel("edge RMSE (pixel)")
    axes[0].set_title("Projective candidate comparison")
    axes[0].legend(fontsize=8)
    axes[0].grid(axis="y", alpha=0.25)

    baseline_errors = [baseline["leave_one_circle_out"][label]["center_error_px"] for label in LABELS]
    axes[1].bar(LABELS, baseline_errors, color="#999999")
    axes[1].axhline(np.mean(baseline_errors), color="#e31a1c", linestyle="--", label="mean")
    axes[1].set_ylabel("held-out center error (pixel)")
    axes[1].set_title("Simple affine/centroid baseline")
    axes[1].legend(fontsize=8)
    axes[1].grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)


def plot_sensitivity(
    threshold_rows: list[dict],
    reference_centers: dict[str, np.ndarray],
    monte_carlo_summary: dict,
    figures_dir: Path,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), dpi=140)
    for label in LABELS:
        local = [row for row in threshold_rows if row["label"] == label]
        thresholds = np.array([row["threshold"] for row in local])
        coordinates = np.array([[row["u_px"], row["v_px"]] for row in local])
        deviations = np.linalg.norm(coordinates - reference_centers[label], axis=1)
        axes[0].plot(thresholds, deviations, marker="o", markersize=3, label=label)
    axes[0].set_xlabel("segmentation threshold")
    axes[0].set_ylabel("center shift from default (pixel)")
    axes[0].set_title("Threshold sensitivity")
    axes[0].grid(alpha=0.25)
    axes[0].legend(ncol=5, fontsize=8)

    radial95 = [monte_carlo_summary[label]["radial_p95_px"] for label in LABELS]
    axes[1].bar(LABELS, radial95, color=plt.cm.tab10(np.arange(5)))
    axes[1].set_ylabel("95th-percentile center shift (pixel)")
    axes[1].set_title("Contour-noise Monte Carlo")
    axes[1].grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)


def make_tex_outputs(
    results_dir: Path,
    selected_model: str,
    centers_rows: list[dict],
    main_metrics: dict,
    validation: dict,
    sensitivity_summary: dict,
    baseline: dict,
) -> None:
    model_cn = "平面单应—圆锥曲线联合模型" if selected_model == "planar_homography" else "已知焦距的相机位姿联合模型"
    cv = validation[selected_model]["summary"]
    lines = [
        "% Generated by code/solve.py; do not edit numerical macros manually.",
        rf"\newcommand{{\MainModelName}}{{{model_cn}}}",
        rf"\newcommand{{\MainEdgeRMSE}}{{{main_metrics['edge']['overall']['rmse_px']:.3f}}}",
        rf"\newcommand{{\MainEdgePctl}}{{{main_metrics['edge']['overall']['p95_abs_px']:.3f}}}",
        rf"\newcommand{{\MainMeanIoU}}{{{main_metrics['mean_silhouette_iou']:.4f}}}",
        rf"\newcommand{{\CVMeanRMSE}}{{{cv['mean_edge_rmse_px']:.3f}}}",
        rf"\newcommand{{\CVMaxRMSE}}{{{cv['max_edge_rmse_px']:.3f}}}",
        rf"\newcommand{{\CVMinIoU}}{{{cv['min_silhouette_iou']:.4f}}}",
        rf"\newcommand{{\BaselineCVError}}{{{baseline['leave_one_circle_out_mean_center_error_px']:.3f}}}",
        rf"\newcommand{{\ThresholdMaxShift}}{{{sensitivity_summary['threshold_max_shift_px']:.3f}}}",
        rf"\newcommand{{\MonteCarloMaxPctl}}{{{sensitivity_summary['monte_carlo_max_radial_p95_px']:.3f}}}",
    ]
    for row in centers_rows:
        label = row["label"]
        lines.extend(
            [
                rf"\newcommand{{\Center{label}u}}{{{row['u_px']:.3f}}}",
                rf"\newcommand{{\Center{label}v}}{{{row['v_px']:.3f}}}",
                rf"\newcommand{{\Center{label}x}}{{{row['camera_x_px']:.3f}}}",
                rf"\newcommand{{\Center{label}y}}{{{row['camera_y_px']:.3f}}}",
            ]
        )
    (results_dir / "generated_numbers.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")

    table_lines = [
        "% Generated by code/solve.py.",
        r"\begin{tabular}{crrrrrr}",
        r"\toprule",
        r"点 & $u$/px & $v$/px & $x$/px & $y$/px & $x$/mm & $y$/mm \\",
        r"\midrule",
    ]
    for row in centers_rows:
        table_lines.append(
            f"{row['label']} & {row['u_px']:.3f} & {row['v_px']:.3f} & "
            f"{row['camera_x_px']:.3f} & {row['camera_y_px']:.3f} & "
            f"{row['camera_x_mm']:.3f} & {row['camera_y_mm']:.3f} \\\\"
        )
    table_lines.extend([r"\bottomrule", r"\end{tabular}"])
    (results_dir / "center_table.tex").write_text("\n".join(table_lines) + "\n", encoding="utf-8")

    summary_lines = [
        "<!-- Generated by code/solve.py. -->",
        f"主模型：{model_cn}",
        "",
        "| 点 | u / px | v / px | x / px | y / px | x / mm | y / mm |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in centers_rows:
        summary_lines.append(
            f"| {row['label']} | {row['u_px']:.3f} | {row['v_px']:.3f} | "
            f"{row['camera_x_px']:.3f} | {row['camera_y_px']:.3f} | "
            f"{row['camera_x_mm']:.3f} | {row['camera_y_mm']:.3f} |"
        )
    summary_lines.extend(
        [
            "",
            f"全样本边界 RMSE：{main_metrics['edge']['overall']['rmse_px']:.3f} px；"
            f"留一圆平均 RMSE：{cv['mean_edge_rmse_px']:.3f} px；"
            f"阈值最大漂移：{sensitivity_summary['threshold_max_shift_px']:.3f} px。",
        ]
    )
    (results_dir / "generated_summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def monte_carlo_repetitions(value: str) -> int:
    repetitions = int(value)
    if repetitions < 2:
        raise argparse.ArgumentTypeError("--monte-carlo must be at least 2")
    return repetitions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        type=Path,
        default=Path("working/source-extract/docx-unpacked/word/media/<SOURCE_FILE_REDACTED>"),
        help="Extracted 1024x768 target image",
    )
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--figures", type=Path, default=Path("figures"))
    parser.add_argument("--monte-carlo", type=monte_carlo_repetitions, default=100)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results_dir = args.results.resolve()
    figures_dir = args.figures.resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    image_path = args.image.resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"Extracted target image not found: {image_path}")

    image = np.asarray(Image.open(image_path).convert("L"))
    if image.shape != (768, 1024):
        raise ValueError(f"Expected image shape (768, 1024), got {image.shape}")
    image_height, image_width = image.shape
    principal = np.array([image_width / 2.0, image_height / 2.0])
    intrinsic = np.array([[FOCAL_PX, 0.0, principal[0]], [0.0, FOCAL_PX, principal[1]], [0.0, 0.0, 1.0]])

    dark_level, bright_level, threshold = dominant_levels(image)
    observation = prepare_observation(image, threshold)

    world = np.vstack([WORLD_CENTERS[label] for label in LABELS])
    ellipse_centers = np.vstack([observation.ellipse_params[label][:2] for label in LABELS])
    initial_h = dlt_homography(world, ellipse_centers)
    free_h, free_fit = fit_homography(observation.boundaries, initial_h)
    pose_h, pose_parameters, pose_fit = fit_pose(observation.boundaries, intrinsic, free_h)

    baseline = baseline_validation(observation)
    free_metrics = candidate_metrics(free_h, observation)
    pose_metrics = candidate_metrics(pose_h, observation)
    validation = leave_one_circle_out(observation, intrinsic, free_h, pose_h)

    free_cv = validation["planar_homography"]["summary"]["mean_edge_rmse_px"]
    pose_cv = validation["calibrated_pose"]["summary"]["mean_edge_rmse_px"]
    # Prefer the physically constrained six-parameter pose only when its held-out
    # error is within 5% of the freer eight-parameter projective model.
    selected_model = "calibrated_pose" if pose_cv <= 1.05 * free_cv else "planar_homography"
    selected_h = pose_h if selected_model == "calibrated_pose" else free_h
    selected_metrics = pose_metrics if selected_model == "calibrated_pose" else free_metrics
    centers = main_centers(selected_h)

    threshold_values = [threshold - 40.0, threshold - 20.0, threshold, threshold + 20.0, threshold + 40.0]
    threshold_rows, threshold_centers = threshold_sensitivity(
        image, threshold_values, intrinsic, selected_model, selected_h
    )
    monte_carlo_samples, monte_carlo_rows = monte_carlo_contours(
        observation,
        intrinsic,
        selected_model,
        selected_h,
        repetitions=args.monte_carlo,
        sigma_px=0.25,
        seed=args.seed,
    )

    threshold_summary = {}
    monte_carlo_summary = {}
    threshold_max_shift = 0.0
    monte_carlo_max_p95 = 0.0
    for label in LABELS:
        threshold_deviation = np.linalg.norm(threshold_centers[label] - centers[label], axis=1)
        threshold_summary[label] = {
            "max_shift_px": float(threshold_deviation.max()),
            "u_range_px": float(np.ptp(threshold_centers[label][:, 0])),
            "v_range_px": float(np.ptp(threshold_centers[label][:, 1])),
        }
        threshold_max_shift = max(threshold_max_shift, float(threshold_deviation.max()))

        deviations = monte_carlo_samples[label] - centers[label]
        radial = np.linalg.norm(deviations, axis=1)
        monte_carlo_summary[label] = {
            "u_std_px": float(np.std(monte_carlo_samples[label][:, 0], ddof=1)),
            "v_std_px": float(np.std(monte_carlo_samples[label][:, 1], ddof=1)),
            "radial_mean_px": float(radial.mean()),
            "radial_p95_px": float(np.percentile(radial, 95)),
        }
        monte_carlo_max_p95 = max(monte_carlo_max_p95, float(np.percentile(radial, 95)))

    sensitivity_summary = {
        "threshold_levels": threshold_values,
        "threshold": threshold_summary,
        "threshold_max_shift_px": threshold_max_shift,
        "monte_carlo_seed": args.seed,
        "monte_carlo_repetitions": args.monte_carlo,
        "contour_noise_sigma_px": 0.25,
        "monte_carlo": monte_carlo_summary,
        "monte_carlo_max_radial_p95_px": monte_carlo_max_p95,
    }

    pose_rotation = Rotation.from_rotvec(pose_parameters[:3]).as_matrix()
    pose_translation = pose_parameters[3:]
    pose_camera_center = -pose_rotation.T @ pose_translation
    depth_samples = pose_depths(pose_parameters)

    center_rows = []
    ellipse_rows = []
    for label in LABELS:
        u, v = centers[label]
        camera_x_px = u - principal[0]
        camera_y_px = principal[1] - v
        baseline_center = observation.component_for_label[label].centroid
        ellipse_center = observation.ellipse_params[label][:2]
        correction = centers[label] - ellipse_center
        center_rows.append(
            {
                "label": label,
                "world_X_mm": float(WORLD_CENTERS[label][0]),
                "world_Y_mm": float(WORLD_CENTERS[label][1]),
                "baseline_u_px": float(baseline_center[0]),
                "baseline_v_px": float(baseline_center[1]),
                "ellipse_center_u_px": float(ellipse_center[0]),
                "ellipse_center_v_px": float(ellipse_center[1]),
                "u_px": float(u),
                "v_px": float(v),
                "camera_x_px": float(camera_x_px),
                "camera_y_px": float(camera_y_px),
                "camera_z_px": FOCAL_PX,
                "camera_x_mm": float(camera_x_px / PIXELS_PER_MM),
                "camera_y_mm": float(camera_y_px / PIXELS_PER_MM),
                "camera_z_mm": float(FOCAL_PX / PIXELS_PER_MM),
                "perspective_correction_du_px": float(correction[0]),
                "perspective_correction_dv_px": float(correction[1]),
                "perspective_correction_norm_px": float(np.linalg.norm(correction)),
            }
        )
        ellipse_parameters = observation.ellipse_params[label]
        ellipse_rows.append(
            {
                "label": label,
                "center_u_px": float(ellipse_parameters[0]),
                "center_v_px": float(ellipse_parameters[1]),
                "semi_major_px": float(math.exp(ellipse_parameters[2])),
                "semi_minor_px": float(math.exp(ellipse_parameters[3])),
                "angle_deg": float(math.degrees(ellipse_parameters[4]) % 180.0),
                "boundary_points": int(len(observation.boundaries[label])),
                "individual_fit_rmse_px": float(
                    np.sqrt(np.mean(sampson_residual(observation.ellipse_conics[label], observation.boundaries[label]) ** 2))
                ),
            }
        )

    source_doc = Path("input/problem/<SOURCE_FILE_REDACTED>")
    source_doc_hash = hashlib.sha256(source_doc.read_bytes()).hexdigest() if source_doc.is_file() else None
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    source_metadata = {
        "status": "pass",
        "source_doc": str(source_doc),
        "source_doc_sha256": source_doc_hash,
        "extracted_image": str(image_path.relative_to(Path.cwd()) if image_path.is_relative_to(Path.cwd()) else image_path),
        "extracted_image_sha256": image_hash,
        "image_width_px": image_width,
        "image_height_px": image_height,
        "dark_mode": dark_level,
        "background_mode": bright_level,
        "segmentation_threshold": threshold,
        "component_count": len(observation.components),
        "assignment_best_rmse_px": observation.assignment_best_rmse,
        "assignment_second_best_rmse_px": observation.assignment_second_rmse,
        "assignment_centroid_best_rmse_px": observation.assignment_centroid_best_rmse,
        "assignment_candidates_checked": observation.assignment_candidates_checked,
        "assignment_metric": "joint equal-radius circle boundary RMSE after homography fit",
    }

    model_metrics = {
        "baseline": baseline,
        "candidates": {
            "planar_homography": {
                "parameters": 8,
                "fit_status": "pass" if free_fit.success else "fail",
                **free_metrics,
            },
            "calibrated_pose": {
                "parameters": 6,
                "fit_status": "pass" if pose_fit.success else "fail",
                **pose_metrics,
            },
        },
        "selection_rule": "choose minimum leave-one-circle-out edge RMSE; prefer 6-parameter pose if within 5% of free homography",
        "selected_model": selected_model,
        "selection_status": "pass",
    }
    validation_summary = {
        "leave_one_circle_out": validation,
        "checks": {
            "five_components": status(len(observation.components) == 5),
            "label_assignment_separation": status(
                observation.assignment_second_rmse > max(5.0 * observation.assignment_best_rmse, 1.0)
            ),
            "main_in_sample_edge_rmse_below_1px": status(
                selected_metrics["edge"]["overall"]["rmse_px"] < 1.0
            ),
            "main_holdout_mean_edge_rmse_below_1px": status(
                validation[selected_model]["summary"]["mean_edge_rmse_px"] < 1.0
            ),
            "main_holdout_min_iou_above_0_94": status(
                validation[selected_model]["summary"]["min_silhouette_iou"] > 0.94
            ),
            "pose_positive_depth": status(float(depth_samples.min()) > 0.0),
            "pose_rotation_orthonormal": status(
                np.linalg.norm(pose_rotation.T @ pose_rotation - np.eye(3), ord="fro") < 1e-10
            ),
            "threshold_max_shift_below_0_5px": status(threshold_max_shift < 0.5),
            "monte_carlo_p95_below_0_5px": status(monte_carlo_max_p95 < 0.5),
        },
        "automatic_mathematical_proof": "needs_review",
    }

    main_results = {
        "case_id": "2008A",
        "status": "pass",
        "selected_model": selected_model,
        "coordinate_convention": {
            "raw_pixels": "u rightward and v downward from the upper-left image corner",
            "principal_point_px": principal,
            "camera_coordinates": "x=u-u0, y=v0-v, z=f; optical center is (0,0,0)",
            "focal_length_px": FOCAL_PX,
            "pixels_per_mm": PIXELS_PER_MM,
        },
        "centers": center_rows,
        "main_metrics": selected_metrics,
        "validation": validation[selected_model]["summary"],
        "sensitivity": sensitivity_summary,
    }

    pose_result = {
        "status": "pass",
        "intrinsic_matrix": intrinsic,
        "rotation_target_to_camera": pose_rotation,
        "translation_target_to_camera_mm": pose_translation,
        "camera_center_in_target_coordinates_mm": pose_camera_center,
        "euler_xyz_deg_for_description_only": Rotation.from_matrix(pose_rotation).as_euler("xyz", degrees=True),
        "minimum_sample_depth_mm": float(depth_samples.min()),
        "maximum_sample_depth_mm": float(depth_samples.max()),
        "homography": pose_h,
        "note": "Pose is an auxiliary calibrated fit; use the selected_model field for reported image centers.",
    }

    save_csv(results_dir / "<SOURCE_FILE_REDACTED>", center_rows)
    save_csv(results_dir / "<SOURCE_FILE_REDACTED>", ellipse_rows)
    save_csv(results_dir / "<SOURCE_FILE_REDACTED>", threshold_rows)
    save_csv(results_dir / "<SOURCE_FILE_REDACTED>", monte_carlo_rows)
    np.savetxt(results_dir / "<SOURCE_FILE_REDACTED>", selected_h, delimiter=",", fmt="%.12g")
    np.savetxt(results_dir / "<SOURCE_FILE_REDACTED>", free_h, delimiter=",", fmt="%.12g")
    write_json(results_dir / "source_metadata.json", source_metadata)
    write_json(results_dir / "model_metrics.json", model_metrics)
    write_json(results_dir / "validation.json", validation_summary)
    write_json(results_dir / "sensitivity.json", sensitivity_summary)
    write_json(results_dir / "main_results.json", main_results)
    write_json(results_dir / "pose.json", pose_result)
    write_json(
        results_dir / "environment.json",
        {
            "status": "pass",
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
            "pillow": Image.__version__,
            "seed": args.seed,
        },
    )

    make_tex_outputs(
        results_dir,
        selected_model,
        center_rows,
        selected_metrics,
        validation,
        sensitivity_summary,
        baseline,
    )
    plot_segmentation(image, observation, figures_dir)
    plot_model_fit(image, observation, selected_h, selected_model, figures_dir)
    plot_model_comparison(baseline, free_metrics, pose_metrics, validation, figures_dir)
    plot_sensitivity(threshold_rows, centers, monte_carlo_summary, figures_dir)

    summary = {
        "status": "pass",
        "selected_model": selected_model,
        "centers_file": str(results_dir / "<SOURCE_FILE_REDACTED>"),
        "edge_rmse_px": selected_metrics["edge"]["overall"]["rmse_px"],
        "holdout_mean_edge_rmse_px": validation[selected_model]["summary"]["mean_edge_rmse_px"],
        "threshold_max_shift_px": threshold_max_shift,
        "monte_carlo_max_radial_p95_px": monte_carlo_max_p95,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
