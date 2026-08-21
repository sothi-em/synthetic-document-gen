import cv2
import numpy as np
import random


def create_mock_document():
    """Generates a clean white document with dummy black text."""
    img = np.ones((1100, 850), dtype=np.uint8) * 255
    font = cv2.FONT_HERSHEY_SIMPLEX

    # Add title
    cv2.putText(
        img, "CONFIDENTIAL TEST DOCUMENT", (70, 100), font, 1.2, 0, 3, cv2.LINE_AA
    )

    # Add body paragraphs
    sentences = [
        "1. Standard Operating Procedures dictate strict adherence to verification guidelines.",
        "2. All system parameters must be carefully calibrated prior to test execution cycles.",
        "3. Automated text recognition systems require a minimum baseline of contrast metrics.",
        "4. Optical Character Recognition (OCR) engines frequently scan for specific stroke widths.",
        "5. Edge degradation and localized artifacts simulate authentic real-world degradation.",
        "6. This pipeline protects text integrity while injecting heavy background noise layers.",
        "7. Final validation metrics will be evaluated against standardized accuracy matrices.",
    ]

    y = 200
    for sentence in sentences:
        cv2.putText(img, sentence, (70, y), font, 0.65, 0, 2, cv2.LINE_AA)
        y += 60

    # Draw some mock structure lines
    cv2.line(img, (70, 130), (780, 130), 0, 2)
    cv2.line(img, (70, 1000), (780, 1000), 0, 2)
    cv2.putText(img, "Page 1 of 1", (380, 1040), font, 0.5, 0, 1, cv2.LINE_AA)

    return img


def apply_aging_effects(clean_img):
    """Applies realistic scanner aging, dirt, and stains while preserving text layout."""
    h, w = clean_img.shape

    # 1. Create paper background color layer (Vintage yellow/beige in BGR)
    paper = np.zeros((h, w, 3), dtype=np.uint8)
    paper[:, :] = [215, 235, 245]  # Soft cream background

    # 2. Add uneven lighting / vignette (darker edges)
    X, Y = np.meshgrid(np.linspace(-1, 1, w), np.linspace(-1, 1, h))
    vignette = 1 - 0.3 * (X**2 + Y**2)
    vignette = np.clip(vignette, 0, 1)
    for i in range(3):
        paper[:, :, i] = (paper[:, :, i] * vignette).astype(np.uint8)

    # 3. Simulate coffee / dirt stains using low-frequency blobs
    stain_mask = np.zeros((h, w), dtype=np.uint8)
    # Generate a few random stain centers
    for _ in range(4):
        cx, cy = random.randint(0, w), random.randint(0, h)
        radius = random.randint(40, 120)
        cv2.circle(stain_mask, (cx, cy), radius, 255, -1)

    # Smooth the stains significantly to make them look organic
    stain_mask = cv2.GaussianBlur(stain_mask, (151, 151), 0)
    stain_mask_norm = stain_mask / 255.0

    # Darken and discolor the stain areas on the paper
    for i, factor in enumerate(
        [0.75, 0.82, 0.88]
    ):  # Differential darkening for brown tint
        paper[:, :, i] = (
            paper[:, :, i] * (1.0 - 0.35 * stain_mask_norm * factor)
        ).astype(np.uint8)

    # 4. Add high-frequency sensor / scanner noise (Salt & Pepper / Grain)
    noise = np.zeros((h, w, 3), dtype=np.int16)
    cv2.randn(noise, 0, 12)  # Gaussian distribution for grain
    paper = np.clip(paper.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # 5. Extract text pixels mask from clean image
    # Anything below 128 is treated as text
    text_mask = clean_img < 128

    # 6. Re-stamp clean text on top of dirty paper
    # Use blending so text isn't absolute harsh black, matching a real fading ink look
    dirty_img = paper.copy()
    for i in range(3):
        # Blend text (approx 85% original dark stroke, 15% underlying stain texture)
        dirty_img[:, :, i] = np.where(
            text_mask,
            int(30 * 0.85) + (paper[:, :, i] * 0.15).astype(np.uint8),
            paper[:, :, i],
        )

    # 7. Apply a subtle global blur to mimic scanner optical focus loss
    dirty_img = cv2.GaussianBlur(dirty_img, (3, 3), 0)

    return dirty_img


if __name__ == "__main__":
    print("Generating clean test document...")
    clean_doc = create_mock_document()

    print("Applying distress, dirt, and aging layers...")
    distressed_doc = apply_aging_effects(clean_doc)

    # Save the output files
    cv2.imwrite("clean_document.png", clean_doc)
    cv2.imwrite("distressed_document.png", distressed_doc)
    print("Success! Saved 'clean_document.png' and 'distressed_document.png'.")
