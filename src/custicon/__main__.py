import sys


def main() -> int:
    if "--capture" in sys.argv:
        from .capture import main as capture_main

        return capture_main(sys.argv)
    from .app import Application

    return Application().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
