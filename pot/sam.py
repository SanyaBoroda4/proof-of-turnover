"""SAM segmentation helpers: turn sam-3.1 output text into boxes."""

import meta_sam_parser


def _object_sort_key(object_id):
    text = str(object_id)
    return (0, int(text)) if text.isdigit() else (1, text)


def detect(output_text, min_confidence=0.5):
    """Detect objects in sam-3.1 output text.

    Returns one {"left", "top", "right", "bottom", "confidence"} box per
    object id, keeping only objects at or above min_confidence (objects
    with unknown confidence pass the filter). Empty output yields no boxes.
    """
    if not output_text or not output_text.strip():
        return []
    parser = meta_sam_parser.image_segmentation_format().create_parser()
    parser.push(output_text)
    finish = parser.finish(meta_sam_parser.CompletedOutcome())
    boxes = {}
    for record in finish.result.records:
        if record.kind == "text":
            continue
        confidence = record.confidence
        if confidence is not None and confidence < min_confidence:
            continue
        if record.kind == "box":
            box = {
                "left": record.left,
                "top": record.top,
                "right": record.right,
                "bottom": record.bottom,
                "confidence": confidence,
            }
        elif record.kind == "mask":
            bounds = record.bounds
            box = {
                "left": bounds.left,
                "top": bounds.top,
                "right": bounds.right,
                "bottom": bounds.bottom,
                "confidence": confidence,
            }
        else:
            continue
        previous = boxes.get(record.object_id)
        if previous is None or (confidence or 0) > (previous["confidence"] or 0):
            boxes[record.object_id] = box
    return [boxes[key] for key in sorted(boxes, key=_object_sort_key)]
