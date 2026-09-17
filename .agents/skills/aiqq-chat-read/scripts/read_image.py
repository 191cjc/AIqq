"""Read a saved image association; the host downloads and stages visual frames."""

import argparse

from _bridge import read


def main() -> None:
    parser = argparse.ArgumentParser(description="Read an image from this group's saved message")
    parser.add_argument("--record-id", type=int, required=True)
    parser.add_argument("--attachment-index", type=int, default=0)
    parser.add_argument("--version-id", type=int)
    arguments = vars(parser.parse_args())
    read("image", {key: value for key, value in arguments.items() if value is not None})


if __name__ == "__main__":
    main()
