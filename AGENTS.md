# Project rules

## Approved architecture

- Python 3.12 or newer
- PySide6 for the desktop GUI
- TCP sockets for networking
- pytest for tests
- JSON for the wire protocol and future persistence

## Development rules

- Keep modules simple and focused.
- Do not change the approved stack.
- Implement one slice at a time.
- Add tests for new behavior.
- Do not rewrite unrelated code.
- Keep deferred features (drawing, chat, GUI networking, and persistence) out until their planned slices.
