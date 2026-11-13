import importlib.util
import sys


def main() -> int:
    if "--capture" in sys.argv and importlib.util.find_spec("custicon.capture") is not None:
        from .capture import main as capture_main

        return capture_main(sys.argv)
    from .app import Application

    return Application().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
