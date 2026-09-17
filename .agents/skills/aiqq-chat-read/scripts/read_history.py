"""Read full same-group records using a stable cursor or exact filters."""

import argparse

from _bridge import read


def main() -> None:
    parser = argparse.ArgumentParser(description="Read complete saved messages from this group")
    parser.add_argument("--before-record-id", type=int)
    parser.add_argument("--record-id", type=int)
    parser.add_argument("--message-id")
    parser.add_argument("--sender")
    parser.add_argument("--keyword")
    parser.add_argument("--sent-after")
    parser.add_argument("--sent-before")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--versions", action="store_true", default=None)
    parser.add_argument("--before-version-id", type=int)
    parser.add_argument("--events", action="store_true", default=None)
    parser.add_argument("--before-event-record-id", type=int)
    arguments = vars(parser.parse_args())
    read("history", {key: value for key, value in arguments.items() if value is not None})


if __name__ == "__main__":
    main()
