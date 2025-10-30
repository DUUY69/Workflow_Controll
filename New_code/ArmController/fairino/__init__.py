# Vendored fairino package loader
# This package is a thin shim that tries to import the SDK's Robot module from
# the local repo (if present), otherwise falls back to the installed "fairino"
# package on PYTHONPATH.

__all__ = ["Robot"]

try:
    # Prefer an included SDK in the repo path (relative to workspace)
    import os
    import sys
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    candidate = os.path.join(base, 'fairino-python-sdk-main', 'windows')
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

    # Try to import Robot from the SDK
    from fairino import Robot  # type: ignore
except Exception:
    # If that fails, try to import the installed package name directly
    try:
        from . import Robot  # type: ignore
    except Exception:
        # Provide a minimal fallback Robot stub to allow graceful errors
        class Robot:
            class RPC:
                def __init__(self, *args, **kwargs):
                    raise ImportError('fairino Robot SDK not available')

            @staticmethod
            def calculate_file_md5(*args, **kwargs):
                raise ImportError('fairino Robot SDK not available')

        # Expose Robot
        pass
