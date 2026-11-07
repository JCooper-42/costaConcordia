import json
import sys
import math
import csv
import io
import random
import time
from datetime import datetime
import pygame

# --- VDR Data Block ---
VDR_DATA = """Time,Ordered Heading/Course,Ordered Rudder Angle,Actual Rudder Angle,Speed,Notes/Context
21:36:02,285.0,,,,1st Deck Officer orders helmsman alongside.
21:39:14,,,,,Master takes command of the watch
21:39:30,300.0,,,3.15,Master orders helmsman to go for 300
21:40:00,310.0,,,16.00,Master orders speed increase to 16 knots
21:40:48,325.0,,,,Helmsman initially replies 315
21:42:07,330.0,,,,Helmsman answers correctly
21:42:40,,,,16.00,Master sends 2nd Officer to the left wing.
21:43:08,335.0,,,,
21:43:33,340.0,,,,
21:43:44,350.0,,,15.90,Helmsman repeats 340
21:44:05,,,,16.00,Ship is 0.5 miles SW of planned route
21:44:11,,10.0,,,Master switches to rudder angle orders
21:44:15,,20.0,,,
21:44:20,,35.0,34.6,,Order range 21:44:20 - 21:44:21.
21:44:36,,0.0,,,Order range 21:44:36 - 21:44:37.
21:44:43,,-10.0,-5.0,,Helmsman executes incorrectly
21:44:45,,-20.0,20.0,,Helmsman erroneously turns 20 starboard
21:45:05,,-35.0,-35.0,,Helmsman runs correctly
21:45:07,,,,8.30,Hull collides with Le Scole rocks
21:45:33,,-35.0,,,Order range 21:45:33 - 21:45:48.
21:45:48,,0.0,0.0,,Helmsman executes correctly
21:46:01,,,,,Helmsman confirms generic Starboard order
21:46:43,,0.0,,,Mid ship executed with no effect
21:46:46,,35.0,,,Steering pumps lose electrical power"""


def parse_vdr_data():
    reader = csv.DictReader(io.StringIO(VDR_DATA.strip()))
    events = []
    for row in reader:
        t_obj = datetime.strptime(row["Time"], "%H:%M:%S")
        secs = t_obj.hour * 3600 + t_obj.minute * 60 + t_obj.second

        events.append({
            "time": secs,
            "heading": float(row["Ordered Heading/Course"]) if row["Ordered Heading/Course"] else None,
            "rudder": float(row["Ordered Rudder Angle"]) if row["Ordered Rudder Angle"] else None,
            "speed": float(row["Speed"]) if row["Speed"] else None
        })

    start_time = events[0]["time"]
    for e in events:
        e["time"] -= start_time
    return events


class GeoDataLoader:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.lines = []
        self.min_lon, self.max_lon = float("inf"), float("-inf")
        self.min_lat, self.max_lat = float("inf"), float("-inf")

    def load(self):
        with open(self.filepath, "r") as geojson:
            data = json.load(geojson)

        self.lines.clear()
        for feature in data.get("features", []):
            geom = feature.get("geometry", {})
            if geom.get("type") == "LineString":
                self._process_line(geom.get("coordinates", []))
        return self.lines

    def _process_line(self, line: list):
        self.lines.append(line)
        for lon, lat in line:
            self.min_lon = min(self.min_lon, lon)
            self.max_lon = max(self.max_lon, lon)
            self.min_lat = min(self.min_lat, lat)
            self.max_lat = max(self.max_lat, lat)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.min_lon, self.max_lon, self.min_lat, self.max_lat


class CoordinateMapper:
    def __init__(self, bounds, width: int, height: int, padding: int = 20, sea_margin_nm: float = 2.0):
        raw_min_lon, raw_max_lon, raw_min_lat, raw_max_lat = bounds
        mean_lat_rad = math.radians((raw_min_lat + raw_max_lat) / 2.0)
        self.lon_correction = math.cos(mean_lat_rad)

        lat_margin_deg = sea_margin_nm / 60.0
        lon_margin_deg = sea_margin_nm / (60.0 * self.lon_correction)

        self.min_lon = raw_min_lon - lon_margin_deg
        self.max_lon = raw_max_lon + lon_margin_deg
        self.min_lat = raw_min_lat - lat_margin_deg
        self.max_lat = raw_max_lat + lat_margin_deg

        self.width_nm = (self.max_lon - self.min_lon) * 60.0 * self.lon_correction
        self.height_nm = (self.max_lat - self.min_lat) * 60.0

        usable_width = width - (padding * 2)
        usable_height = height - (padding * 2)

        scale_x = usable_width / self.width_nm if self.width_nm > 0 else 1.0
        scale_y = usable_height / self.height_nm if self.height_nm > 0 else 1.0
        self.pixels_per_nm = min(scale_x, scale_y)

        self.offset_x = (width - (self.width_nm * self.pixels_per_nm)) / 2.0
        self.offset_y = (height - (self.height_nm * self.pixels_per_nm)) / 2.0

    def map_point(self, lon: float, lat: float) -> tuple[float, float]:
        x_nm = (lon - self.min_lon) * 60.0 * self.lon_correction
        y_nm = (lat - self.min_lat) * 60.0

        x = x_nm * self.pixels_per_nm + self.offset_x
        y = self.offset_y + (self.height_nm - y_nm) * self.pixels_per_nm
        return x, y

    def pixel_to_gps(self, x: float, y: float) -> tuple[float, float]:
        """Inverse mapping from screen pixels back to (Lon, Lat)."""
        x_nm = (x - self.offset_x) / self.pixels_per_nm
        y_nm = self.height_nm - ((y - self.offset_y) / self.pixels_per_nm)

        lon = self.min_lon + (x_nm / (60.0 * self.lon_correction))
        lat = self.min_lat + (y_nm / 60.0)
        return lon, lat

    def map_lines(self, lines: list) -> list:
        return [[self.map_point(lon, lat) for lon, lat in line] for line in lines]


class FastCoastlineCollider:
    """Pre-computes line segment math and bounding boxes for fast headless collision checks."""
    def __init__(self, pixel_lines: list):
        self.segments = []
        self.min_x, self.max_x = float("inf"), float("-inf")
        self.min_y, self.max_y = float("inf"), float("-inf")

        for line in pixel_lines:
            for i in range(len(line) - 1):
                x1, y1 = line[i]
                x2, y2 = line[i + 1]
                dx = x2 - x1
                dy = y2 - y1
                seg_len_sq = dx * dx + dy * dy
                self.segments.append((x1, y1, dx, dy, seg_len_sq))

                self.min_x = min(self.min_x, x1, x2)
                self.max_x = max(self.max_x, x1, x2)
                self.min_y = min(self.min_y, y1, y2)
                self.max_y = max(self.max_y, y1, y2)

    def distance_sq_to_coast(self, x: float, y: float) -> float:
        min_dist_sq = float("inf")
        for x1, y1, dx, dy, seg_len_sq in self.segments:
            if seg_len_sq == 0:
                cx, cy = x1, y1
            else:
                t = ((x - x1) * dx + (y - y1) * dy) / seg_len_sq
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                cx = x1 + t * dx
                cy = y1 + t * dy

            dist_sq = (x - cx) ** 2 + (y - cy) ** 2
            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
        return min_dist_sq

    def check_collision(self, x: float, y: float, radius: float) -> bool:
        # Fast bounding-box rejection before checking individual segments
        if x < self.min_x - radius or x > self.max_x + radius or y < self.min_y - radius or y > self.max_y + radius:
            return False
        return self.distance_sq_to_coast(x, y) <= (radius ** 2)


class Ship:
    def __init__(self, x_coord, y_coord, pixels_per_nm, heading=0.0, speed=0.0, radius=5.0):
        self.x = float(x_coord)
        self.y = float(y_coord)
        self.pixels_per_nm = pixels_per_nm
        self.heading = heading
        self.target_heading = heading
        self.rudder_angle = 0.0
        self.speed = speed
        self.radius = radius
        self.mode = "heading"

    def apply_command(self, heading, rudder, speed):
        if heading is not None:
            self.target_heading = heading
            self.mode = "heading"
        if rudder is not None:
            self.rudder_angle = rudder
            self.mode = "rudder"
        if speed is not None:
            self.speed = speed

    def update(self, sim_dt: float):
        turn_rate_max = 0.7
        if self.mode == "heading":
            diff = (self.target_heading - self.heading + 180) % 360 - 180
            if diff > 1:
                self.rudder_angle = min(35.0, diff)
            elif diff < -1:
                self.rudder_angle = max(-35.0, diff)
            else:
                self.rudder_angle = 0.0
                self.heading = self.target_heading

        actual_turn = (self.rudder_angle / 35.0) * turn_rate_max * sim_dt
        self.heading = (self.heading + actual_turn) % 360

        rad_heading = math.radians(self.heading)
        distance_px = (self.speed / 3600.0) * sim_dt * self.pixels_per_nm
        self.x += math.sin(rad_heading) * distance_px
        self.y -= math.cos(rad_heading) * distance_px


def run_single_voyage(start_x, start_y, vdr_events, collider, pixels_per_nm, sim_dt=1.0, record_path=False):
    """Runs a pure-math voyage from (start_x, start_y) with no graphics."""
    first = vdr_events[0]
    s = Ship(
        start_x,
        start_y,
        pixels_per_nm=pixels_per_nm,
        heading=first["heading"] if first["heading"] is not None else 285.0,
        speed=first["speed"] if first["speed"] is not None else 15.8,
        radius=5.0
    )
    s.apply_command(first["heading"], first["rudder"], first["speed"])

    sim_time = 0.0
    max_time = vdr_events[-1]["time"]
    event_idx = 1
    path = [(s.x, s.y)] if record_path else None

    while sim_time <= max_time:
        sim_time += sim_dt

        while event_idx < len(vdr_events) and sim_time >= vdr_events[event_idx]["time"]:
            ev = vdr_events[event_idx]
            s.apply_command(ev["heading"], ev["rudder"], ev["speed"])
            event_idx += 1

        s.update(sim_dt)
        if record_path:
            path.append((s.x, s.y))

        if collider.check_collision(s.x, s.y, s.radius):
            return {"collided": True, "time": sim_time, "impact_pos": (s.x, s.y), "path": path}

    return {"collided": False, "time": sim_time, "impact_pos": None, "path": path}


def compute_event_horizon(results, num_bins=15):
    """Finds the maximum longitude that still results in a collision for each latitude band."""
    lats = [r["start_lat"] for r in results]
    min_lat, max_lat = min(lats), max(lats)
    bin_size = (max_lat - min_lat) / num_bins

    horizon = []
    for b in range(num_bins):
        lat_low = min_lat + b * bin_size
        lat_high = lat_low + bin_size
        bin_Collisions = [
            r["start_lon"] for r in results
            if r["collided"] and lat_low <= r["start_lat"] < lat_high
        ]
        if bin_Collisions:
            # The furthest east (highest longitude) start point that still hits the island
            crit_lon = max(bin_Collisions)
            horizon.append((crit_lon, (lat_low + lat_high) / 2.0))
    return horizon


if __name__ == "__main__":
    SCREEN_WIDTH, SCREEN_HEIGHT = 800, 800
    loader = GeoDataLoader("coastline.geojson")
    raw_lines = loader.load()

    mapper = CoordinateMapper(loader.bounds, SCREEN_WIDTH, SCREEN_HEIGHT, sea_margin_nm=2.0)
    pixel_lines = mapper.map_lines(raw_lines)
    collider = FastCoastlineCollider(pixel_lines)
    vdr_events = parse_vdr_data()

    # 1. Verify Historical Baseline First
    HIST_LON, HIST_LAT = 10.9628, 42.3319
    hist_x, hist_y = mapper.map_point(HIST_LON, HIST_LAT)
    baseline = run_single_voyage(hist_x, hist_y, vdr_events, collider, mapper.pixels_per_nm, sim_dt=0.5, record_path=True)

    print("=== HISTORICAL BASELINE VERIFICATION ===")
    print(f"Start: ({HIST_LON}, {HIST_LAT}) -> Collided: {baseline['collided']} at t = {baseline['time']:.1f}s (VDR impact is ~545s)\n")

    # 2. Run Headless Monte Carlo Simulation
    NUM_RUNS = 2000
    # Sample region to the East/Southeast of Giglio Island
    LON_RANGE = (10.9300, 11.0000)
    LAT_RANGE = (42.3000, 42.3700)

    print(f"Running {NUM_RUNS} headless Monte Carlo simulations...")
    t0 = time.perf_counter()

    mc_results = []
    for _ in range(NUM_RUNS):
        s_lon = random.uniform(*LON_RANGE)
        s_lat = random.uniform(*LAT_RANGE)
        sx, sy = mapper.map_point(s_lon, s_lat)

        # Skip points that spawn directly on land
        if collider.check_collision(sx, sy, radius=5.0):
            continue

        outcome = run_single_voyage(sx, sy, vdr_events, collider, mapper.pixels_per_nm, sim_dt=1.0)
        mc_results.append({
            "start_lon": s_lon,
            "start_lat": s_lat,
            "start_px": (sx, sy),
            "collided": outcome["collided"],
            "time": outcome["time"],
            "impact_px": outcome["impact_pos"]
        })

    elapsed = time.perf_counter() - t0

    # 3. Statistical Output
    total_valid = len(mc_results)
    collisions = [r for r in mc_results if r["collided"]]
    safe_runs = [r for r in mc_results if not r["collided"]]
    collision_rate = (len(collisions) / total_valid) * 100 if total_valid else 0

    print(f"Completed {total_valid} valid ocean starts in {elapsed:.2f} seconds.")
    print(f"Collision Probability in Zone: {collision_rate:.1f}% ({len(collisions)} collisions, {len(safe_runs)} safe)")

    if collisions:
        avg_time = sum(r["time"] for r in collisions) / len(collisions)
        min_time = min(r["time"] for r in collisions)
        max_time = max(r["time"] for r in collisions)
        print(f"Time to Impact -> Mean: {avg_time:.1f}s | Min: {min_time:.1f}s | Max: {max_time:.1f}s")

    # 4. Event Horizon Table
    horizon_gps = compute_event_horizon(mc_results, num_bins=12)
    print("\n=== EVENT HORIZON (Critical Longitude per Latitude Band) ===")
    print("Ships starting WEST of this longitude for a given latitude are bound to collide:")
    for crit_lon, mid_lat in horizon_gps:
        print(f"  Latitude {mid_lat:.4f}°N  -->  Critical Longitude: {crit_lon:.4f}°E")

    # 5. Visual Verification Plot (Static Map)
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Monte Carlo Verification: Event Horizon & Outcomes")
    screen.fill((0, 0, 0))

    # Draw Start Positions: Green = Safe, Red = Will Collide
    for r in mc_results:
        color = (220, 50, 50) if r["collided"] else (40, 180, 70)
        pygame.draw.circle(screen, color, (int(r["start_px"][0]), int(r["start_px"][1])), 2)

    # Draw Event Horizon Boundary Line (Yellow)
    if len(horizon_gps) > 1:
        horizon_px = [mapper.map_point(lon, lat) for lon, lat in horizon_gps]
        pygame.draw.lines(screen, (255, 230, 0), False, horizon_px, 3)

    # Draw Historical Trajectory (Cyan)
    if baseline["path"] and len(baseline["path"]) > 1:
        pygame.draw.lines(screen, (0, 255, 255), False, baseline["path"], 2)

    # Draw Coastline (White)
    for line in pixel_lines:
        if len(line) > 1:
            pygame.draw.lines(screen, (255, 255, 255), False, line, 2)

    pygame.display.flip()

    # Keep verification window open until closed
    viewing = True
    while viewing:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                viewing = False
    pygame.quit()
    sys.exit()