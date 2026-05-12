"""Multi-function test module for MonoSwarm end-to-end testing."""
from typing import Union


def add(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    """Add two numbers."""
    return a + b


def subtract(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    """Subtract b from a."""
    return a - b


def multiply(a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
    """Multiply two numbers."""
    return a * b


# --- Pytest test functions ---
def test_add():
    assert add(3, 5) == 8
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_subtract():
    assert subtract(10, 4) == 6
    assert subtract(5, 5) == 0
    assert subtract(0, 3) == -3


def test_multiply():
    assert multiply(6, 7) == 42
    assert multiply(-2, 3) == -6
    assert multiply(0, 999) == 0


if __name__ == "__main__":
    print("add(3, 5) =", add(3, 5))
    print("subtract(10, 4) =", subtract(10, 4))
    print("multiply(6, 7) =", multiply(6, 7))
