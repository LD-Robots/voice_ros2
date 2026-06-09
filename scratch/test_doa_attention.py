import math

def get_circular_average(angles):
    if not angles:
        return -1
    x_sum = sum(math.cos(math.radians(a)) for a in angles)
    y_sum = sum(math.sin(math.radians(a)) for a in angles)
    avg_rad = math.atan2(y_sum, x_sum)
    avg_deg = math.degrees(avg_rad)
    return int(round(avg_deg)) % 360

def angular_distance(a, b):
    diff = (a - b + 180) % 360 - 180
    return abs(diff)

# Tests
def test_circular_average():
    # Test simple average
    assert get_circular_average([10, 20, 30]) == 20
    # Test circular wrap-around
    assert get_circular_average([350, 0, 10]) == 0
    assert get_circular_average([359, 1]) == 0
    # Test empty list
    assert get_circular_average([]) == -1
    print("get_circular_average tests passed!")

def test_angular_distance():
    # Standard distance
    assert angular_distance(10, 20) == 10
    # Circular distance across 0/360 boundary
    assert angular_distance(350, 10) == 20
    assert angular_distance(10, 350) == 20
    assert angular_distance(0, 180) == 180
    assert angular_distance(180, 0) == 180
    assert angular_distance(90, 270) == 180
    print("angular_distance tests passed!")

if __name__ == "__main__":
    test_circular_average()
    test_angular_distance()
    print("All tests passed successfully!")
