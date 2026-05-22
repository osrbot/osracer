"""Ellipsoid-fitting based magnetometer calibration (hard-iron + soft-iron)."""

import numpy as np


def fit_ellipsoid(data: np.ndarray):
    """Fit an ellipsoid to N×3 data via algebraic least-squares.

    Normalises data before fitting to improve numerical conditioning.
    Returns (center, radii, eigvecs) in original units.

    Raises ValueError when the result is not a valid ellipsoid (e.g. too
    little rotation coverage or collinear samples).
    """
    norm_scale = np.max(np.abs(data))
    if norm_scale < 1e-30:
        raise ValueError("Data is all zeros")
    d = data / norm_scale

    x, y, z = d[:, 0], d[:, 1], d[:, 2]
    D = np.column_stack([
        x*x, y*y, z*z,
        2*x*y, 2*x*z, 2*y*z,
        2*x, 2*y, 2*z,
        np.ones_like(x),
    ])

    # Smallest eigenvector of D^T D is the best-fit quadric coefficient vector
    _, vecs = np.linalg.eigh(D.T @ D)
    v = vecs[:, 0]

    M3 = np.array([
        [v[0], v[3], v[4]],
        [v[3], v[1], v[5]],
        [v[4], v[5], v[2]],
    ])
    u = v[6:9]
    j = v[9]

    try:
        M3_inv = np.linalg.inv(M3)
    except np.linalg.LinAlgError:
        raise ValueError("Ellipsoid fit failed: singular shape matrix")

    center = (-M3_inv @ u) * norm_scale

    denom = u @ M3_inv @ u - j
    if abs(denom) < 1e-30:
        raise ValueError("Ellipsoid fit failed: degenerate quadric")

    eigvals, eigvecs = np.linalg.eigh(M3 / denom)
    if np.any(eigvals <= 0):
        raise ValueError(
            "Fit result is not an ellipsoid (non-positive eigenvalues). "
            "Rotate the sensor in all 3 axes before stopping calibration."
        )

    radii = (1.0 / np.sqrt(eigvals)) * norm_scale
    return center, radii, eigvecs


def compute_calibration(data: np.ndarray):
    """Return (hard_iron, soft_iron_matrix) from N×3 raw magnetometer samples.

    Calibrated field = soft_iron_matrix @ (raw - hard_iron)
    """
    center, radii, eigvecs = fit_ellipsoid(data)
    mean_radius = np.mean(radii)
    soft_iron = eigvecs @ np.diag(mean_radius / radii) @ eigvecs.T
    return center, soft_iron
