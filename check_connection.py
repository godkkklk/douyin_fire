"""Read-only diagnostic; never sends or fills a chat composer."""
import gui

if __name__ == '__main__':
    try:
        settings = gui.read_json(gui.SETTINGS, {})
        data = gui.validate(settings)
        gui.send_round(data, True, lambda kind, value: print(value, flush=True))
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), flush=True)
        raise SystemExit(1)
