"""Small test helpers used when ``unittest.mock`` is unavailable.

Some constrained Windows environments fail while importing ``unittest.mock``
because that module imports ``asyncio`` and the local socket provider cannot
initialize ``_overlapped``.  CI and normal developer machines should keep using
the standard library mock implementation; this fallback only implements the
subset used by this repository's tests and synthetic benchmark.
"""

import functools
import importlib
import os
from collections import namedtuple


Call = namedtuple("Call", "args kwargs")
_DEFAULT = object()


class Mock:
    """Minimal callable test double with call tracking."""

    def __init__(self, *args, **kwargs):
        self.return_value = kwargs.pop("return_value", None)
        self.side_effect = kwargs.pop("side_effect", None)
        self.call_args_list = []
        self.call_count = 0
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __call__(self, *args, **kwargs):
        self.call_count += 1
        self.call_args_list.append(Call(args=args, kwargs=kwargs))
        if self.side_effect is not None:
            if callable(self.side_effect):
                return self.side_effect(*args, **kwargs)
            if isinstance(self.side_effect, BaseException):
                raise self.side_effect
            try:
                next_value = next(self._side_effect_iter)
            except AttributeError:
                self._side_effect_iter = iter(self.side_effect)
                next_value = next(self._side_effect_iter)
            if isinstance(next_value, BaseException):
                raise next_value
            return next_value
        return self.return_value

    def assert_not_called(self):
        if self.call_count:
            raise AssertionError(f"Expected mock not to be called. Called {self.call_count} times.")

    def assert_called_once(self):
        if self.call_count != 1:
            raise AssertionError(f"Expected mock to be called once. Called {self.call_count} times.")

    def assert_any_call(self, *args, **kwargs):
        expected = Call(args=args, kwargs=kwargs)
        if expected not in self.call_args_list:
            raise AssertionError(f"Expected call not found: {expected!r}")


class _Patch:
    def __init__(self, target, attribute=None, new=_DEFAULT, **mock_kwargs):
        self.target = target
        self.attribute = attribute
        self.new = new
        self.mock_kwargs = mock_kwargs
        self._had_original = False
        self._original = None
        self._replacement = None
        self._inject = new is _DEFAULT

    def __call__(self, func):
        patchings = getattr(func, "_simple_patchings", None)
        if patchings is not None:
            patchings.append(self)
            return func

        patchings = [self]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            started = []
            injected = []
            try:
                for patching in patchings:
                    value = patching.start()
                    started.append(patching)
                    if patching._inject:
                        injected.append(value)
                return func(*args, *injected, **kwargs)
            finally:
                for patching in reversed(started):
                    patching.stop()

        wrapper._simple_patchings = patchings
        return wrapper

    def __enter__(self):
        return self.start()

    def __exit__(self, _exc_type, _exc, _tb):
        self.stop()
        return False

    def start(self):
        owner, attr = self._resolve_target()
        self._owner = owner
        self._attr = attr
        self._had_original = hasattr(owner, attr)
        self._original = getattr(owner, attr, None)
        if self.new is _DEFAULT:
            replacement = Mock(**self.mock_kwargs)
        else:
            replacement = self.new
        setattr(owner, attr, replacement)
        self._replacement = replacement
        return replacement

    def stop(self):
        if self._had_original:
            setattr(self._owner, self._attr, self._original)
        else:
            delattr(self._owner, self._attr)

    def _resolve_target(self):
        if self.attribute is not None:
            return self.target, self.attribute
        parts = self.target.split(".")
        if len(parts) < 2:
            raise ValueError(f"Invalid patch target: {self.target!r}")
        import_error = None
        for index in range(len(parts) - 1, 0, -1):
            module_name = ".".join(parts[:index])
            try:
                owner = importlib.import_module(module_name)
            except ModuleNotFoundError as exc:
                import_error = exc
                continue
            for attr_name in parts[index:-1]:
                owner = getattr(owner, attr_name)
            return owner, parts[-1]
        if import_error is not None:
            raise import_error
        raise ValueError(f"Invalid patch target: {self.target!r}")


class _PatchDict:
    def __init__(self, mapping, values=(), clear=False, **kwargs):
        self.mapping = mapping
        self.values = dict(values, **kwargs)
        self.clear = clear

    def __call__(self, func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)
        return wrapper

    def __enter__(self):
        self._original = dict(self.mapping)
        if self.clear:
            self.mapping.clear()
        self.mapping.update(self.values)
        return self.mapping

    def __exit__(self, _exc_type, _exc, _tb):
        self.mapping.clear()
        self.mapping.update(self._original)
        return False


class _PatchFactory:
    def __call__(self, target, new=_DEFAULT, **kwargs):
        return _Patch(target, new=new, **kwargs)

    def object(self, target, attribute, new=_DEFAULT, **kwargs):
        return _Patch(target, attribute=attribute, new=new, **kwargs)

    def dict(self, mapping, values=(), clear=False, **kwargs):
        if isinstance(mapping, str):
            if mapping != "os.environ":
                raise ValueError(f"Unsupported mapping target: {mapping!r}")
            mapping = os.environ
        return _PatchDict(mapping, values=values, clear=clear, **kwargs)


patch = _PatchFactory()
