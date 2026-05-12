result = sum(i**2 for i in range(1, 101))
with open("/tmp/sum_of_squares.txt", "w") as f:
    f.write(str(result))
