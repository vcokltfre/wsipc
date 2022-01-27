from inspect import iscoroutinefunction
from typing import Any, Awaitable, Callable, TypeVar, Union

T = TypeVar("T")

Callback = Callable[[Any], Union[None, Awaitable[None]]]


async def maybe_async(func: Callback, *args, **kwargs) -> None:
    if iscoroutinefunction(func):
        return await func(*args, **kwargs)  # type: ignore
    return func(*args, **kwargs)  # type: ignore
