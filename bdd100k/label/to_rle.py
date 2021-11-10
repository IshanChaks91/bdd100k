"""Convert poly2d to rle."""
import argparse
import os
from functools import partial
from multiprocessing import Pool
from typing import Callable, Dict, List

from scalabel.common.parallel import NPROC
from scalabel.label.io import group_and_sort, load, save
from scalabel.label.transforms import frame_to_rles, rle_to_box2d
from scalabel.label.typing import Config, Frame, ImageSize, Poly2D
from tqdm import tqdm

from ..common.logger import logger
from ..common.typing import BDD100KConfig
from ..common.utils import load_bdd100k_config
from .to_scalabel import bdd100k_to_scalabel

ToRLEsFunc = Callable[[List[Frame], str, Config, int], None]


def parse_args() -> argparse.Namespace:
    """Parse arguments."""
    parser = argparse.ArgumentParser(description="poly2d/mask to rle format")
    parser.add_argument(
        "-i",
        "--input",
        help=(
            "root directory of bdd100k label Json files or path to a label "
            "json file"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help="path to save rle formatted label file",
    )
    parser.add_argument(
        "-m",
        "--mode",
        default="sem_seg",
        choices=[
            "sem_seg",
            "drivable",
            "lane_mark",
            "pan_seg",
            "ins_seg",
            "seg_track",
        ],
        help="conversion mode.",
    )
    parser.add_argument(
        "--nproc",
        type=int,
        default=NPROC,
        help="number of processes for conversion",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Configuration file",
    )
    return parser.parse_args()


def frames_to_rle(
    out_path: str, shape: ImageSize, frames: List[Frame]
) -> None:
    """Converting a frame of poly2ds to rle."""
    for image_anns in frames:
        poly2ds: List[List[Poly2D]] = []
        labels_ = image_anns.labels
        if labels_ is None or len(labels_) == 0:
            continue
        # Scores higher, rendering later
        has_score = all((label.score is not None for label in labels_))
        if has_score:
            labels_ = sorted(
                labels_, key=lambda label: float(label.score)  # type: ignore
            )
        for label in labels_:
            if label.poly2d is None:
                continue
            poly2ds.append(label.poly2d)
        rles = frame_to_rles(shape, poly2ds)
        for label, rle in zip(labels_, rles):
            label.rle = rle
            label.box2d = rle_to_box2d(rle)
    save(out_path, frames)


def frames_to_rles(
    nproc: int,
    out_paths: List[str],
    shapes: List[ImageSize],
    frames_list: List[List[Frame]],
) -> None:
    """Execute the rle conversion in parallel."""
    with Pool(nproc) as pool:
        pool.starmap(
            partial(frames_to_rle),
            tqdm(
                zip(out_paths, shapes, frames_list),
                total=len(out_paths),
            ),
        )


def seg_to_rles(  # pylint: disable=unused-argument
    frames: List[Frame], out_path: str, config: Config, nproc: int = NPROC
) -> None:
    """Converting segmentation poly2d to rles."""
    img_shape = config.imageSize
    assert img_shape is not None, "Seg conversion requires imageSize in config"
    logger.info("Start conversion for Seg to RLEs")
    frames_to_rle(out_path, img_shape, frames)


def segtrack_to_rles(
    frames: List[Frame], out_base: str, config: Config, nproc: int = NPROC
) -> None:
    """Converting segmentation tracking poly2d to rles."""
    frames_list = group_and_sort(frames)
    os.makedirs(out_base, exist_ok=True)
    img_shape = config.imageSize
    assert img_shape is not None, "Conversion requires imageSize in config."

    logger.info("Preparing annotations for SegTrack to RLEs")
    out_paths: List[str] = []
    shapes: List[ImageSize] = []
    for video_anns in frames_list:
        video_name = video_anns[0].videoName
        assert (
            video_name is not None
        ), "SegTrack conversion requires videoName in annotations"
        out_paths.append(os.path.join(out_base, f"{video_name}.json"))
        shapes.append(img_shape)

    logger.info("Start Conversion for SegTrack to RLEs")
    frames_to_rles(nproc, out_paths, shapes, frames_list)


def main() -> None:
    """Main function."""
    args = parse_args()
    assert args.mode in [
        "sem_seg",
        "drivable",
        # "lane_mark",
        "pan_seg",
        "ins_seg",
        "seg_track",
    ]
    os.environ["QT_QPA_PLATFORM"] = "offscreen"  # matplotlib offscreen render

    convert_funcs: Dict[str, ToRLEsFunc] = dict(
        sem_seg=seg_to_rles,
        drivable=seg_to_rles,
        # lane_mark=lanemark_to_rles,
        pan_seg=seg_to_rles,
        ins_seg=seg_to_rles,
        seg_track=segtrack_to_rles,
    )

    dataset = load(args.input, args.nproc)
    if args.config is not None:
        bdd100k_config = load_bdd100k_config(args.config)
    elif dataset.config is not None:
        bdd100k_config = BDD100KConfig(config=dataset.config)
    else:
        bdd100k_config = load_bdd100k_config(args.mode)

    if args.mode in ["ins_seg", "seg_track"]:
        frames = bdd100k_to_scalabel(dataset.frames, bdd100k_config)
    else:
        frames = dataset.frames

    convert_funcs[args.mode](
        frames, args.output, bdd100k_config.scalabel, args.nproc
    )

    logger.info("Finished!")


if __name__ == "__main__":
    main()
