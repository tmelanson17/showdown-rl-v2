import time

from doesitlive.server import load_damages, get_adjusted_damage

if __name__ == "__main__":
    load_begin = time.time()
    data = load_damages("find_set/power_levels.csv")
    load_end = time.time()
    print(f"Load time: {load_end - load_begin}")
    begin_time = time.time()
    for _ in range(1000):
        get_adjusted_damage(*data["Physical"], 100.0)
        get_adjusted_damage(*data["Special"], 130.0)

    end_time = time.time()
    print(f"Time taken: {end_time - begin_time}")
