import os
import sys

if __name__ == "__main__":
    if "--selftest" in sys.argv:           # used by CI: everything imports, ffmpeg answers
        import edge_tts, faster_whisper, numpy, requests  # noqa: F401
        from PySide6 import QtWidgets  # noqa: F401
        from recapmm import media, units, translator, pipeline, tools, textimg, clone, dubbing, movie, subtitle  # noqa: F401
        print("imports ok; ffmpeg:", media.check_ffmpeg())
        sys.exit(0)
    if "--render-test" in sys.argv:        # used by CI: build the window headless, save screenshots
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from recapmm.app import render_test
        render_test(".")
        sys.exit(0)
    from recapmm.app import main
    main()
