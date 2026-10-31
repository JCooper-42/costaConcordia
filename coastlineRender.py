import json
import sys
import pygame
import math
import csv
import io
from datetime import datetime

# --- VDR Data Block ---
VDR_DATA = """Time,Ordered Heading/Course,Ordered Rudder Angle,Actual Rudder Angle,Speed,Notes/Context
21:36:02,285.0,,,,1st Deck Officer orders helmsman alongside.
21:39:14,,,,,Master takes command of the watch
21:39:30,300.0,,,15.80,Master orders helmsman to go for 300
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

        # Prefer Actual Rudder Angle if recorded (e.g. helmsman error at 21:44:45), else Ordered
        rudder_str = row["Actual Rudder Angle"] or row["Ordered Rudder Angle"]

        events.append({
            "time": secs,
            "heading": float(row["Ordered Heading/Course"]) if row["Ordered Heading/Course"] else None,
            "rudder": float(rudder_str) if rudder_str else None,
            "speed": float(row["Speed"]) if row["Speed"] else None
        })

    # Normalize timestamps so the first event starts at t=0 seconds
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
            geom_type = geom.get("type")
            coords = geom.get("coordinates", [])

            if geom_type == "LineString":
                self._process_line(coords)
            elif geom_type == "MultiLineString":
                for line in coords:
                    self._process_line(line)
            elif geom_type == "Polygon":
                for ring in coords:
                    self._process_line(ring)

        return self.lines

    def _process_line(self, line: list):
        self.lines.append(line)
        for lon, lat in line:
            if lon < self.min_lon:
                self.min_lon = lon
            if lon > self.max_lon:
                self.max_lon = lon
            if lat < self.min_lat:
                self.min_lat = lat
            if lat > self.max_lat:
                self.max_lat = lat

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.min_lon, self.max_lon, self.min_lat, self.max_lat


class CoordinateMapper:
    """Converts geographic GPS coordinates (Lon/Lat) to pixel screen coordinates (X, Y)
    while preserving true Nautical Mile (NM) scale and aspect ratio."""
    def __init__(self, bounds: tuple[float, float, float, float], width: int, height: int, padding: int = 20):
        self.min_lon, self.max_lon, self.min_lat, self.max_lat = bounds

        # 1 degree of latitude = 60 Nautical Miles (NM)
        # 1 degree of longitude = 60 * cos(latitude) NM
        mean_lat_rad = math.radians((self.min_lat + self.max_lat) / 2.0)
        self.lon_correction = math.cos(mean_lat_rad)

        # Total bounding box dimensions in Nautical Miles
        self.width_nm = (self.max_lon - self.min_lon) * 60.0 * self.lon_correction
        self.height_nm = (self.max_lat - self.min_lat) * 60.0

        usable_width = width - (padding * 2)
        usable_height = height - (padding * 2)

        scale_x = usable_width / self.width_nm if self.width_nm > 0 else 1.0
        scale_y = usable_height / self.height_nm if self.height_nm > 0 else 1.0

        # Exact number of screen pixels per Nautical Mile
        self.pixels_per_nm = min(scale_x, scale_y)

        self.offset_x = (width - (self.width_nm * self.pixels_per_nm)) / 2.0
        self.offset_y = (height - (self.height_nm * self.pixels_per_nm)) / 2.0

    def map_point(self, lon: float, lat: float) -> tuple[int, int]:
        x_nm = (lon - self.min_lon) * 60.0 * self.lon_correction
        y_nm = (lat - self.min_lat) * 60.0

        x = x_nm * self.pixels_per_nm + self.offset_x
        y = self.offset_y + (self.height_nm - y_nm) * self.pixels_per_nm
        return int(x), int(y)

    def map_lines(self, lines: list) -> list:
        """Batch transforms raw GPS coordinate lines into pixel lines."""
        return [[self.map_point(lon, lat) for lon, lat in line] for line in lines]


class DisplayManager:
    BG_COLOR = (0, 0, 0)
    COAST_COLOR = (255, 255, 255)
    TRAIL_COLOR = (100, 180, 255)

    def __init__(self, screen_lines: list, width: int = 800, height: int = 800, title: str = "Costa Concordia Simulator"):
        pygame.init()
        self.screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption(title)
        self.screen_lines = screen_lines

    def clear(self):
        self.screen.fill(self.BG_COLOR)

    def draw_coastline(self, line_width: int = 2):
        for line in self.screen_lines:
            if len(line) > 1:
                pygame.draw.lines(self.screen, self.COAST_COLOR, False, line, line_width)

    def draw_ship(self, s):
        if s is not None:
            if len(s.trail) > 1:
                pygame.draw.lines(self.screen, self.TRAIL_COLOR, False, s.trail, 2)
            pygame.draw.circle(self.screen, "red", (int(s.x), int(s.y)), 5)

    def refresh(self):
        pygame.display.flip()


class Ship:
    def __init__(self, x_coord: float, y_coord: float, pixels_per_nm: float, heading: float = 0.0, speed: float = 0.0):
        self.x = float(x_coord)
        self.y = float(y_coord)
        self.pixels_per_nm = pixels_per_nm
        self.heading = heading
        self.target_heading = heading
        self.rudder_angle = 0.0
        self.speed = speed
        self.mode = "heading"  # 'heading' (autopilot/helmsman course) or 'rudder' (direct rudder angle)
        self.trail = [(int(self.x), int(self.y))]

    def apply_command(self, heading, rudder, speed):
        """Updates ship target parameters based on a VDR event."""
        if heading is not None:
            self.target_heading = heading
            self.mode = "heading"
        if rudder is not None:
            self.rudder_angle = rudder
            self.mode = "rudder"
        if speed is not None:
            self.speed = speed

    def update(self, sim_dt: float):
        """Advances ship physics by sim_dt (simulation seconds)."""
        # Max turn rate in degrees per simulation second at full 35-degree rudder (~0.9 deg/sec for a large cruise ship)
        turn_rate_max_deg_per_sec = 0.9

        # 1. Steering Logic
        if self.mode == "heading":
            diff = (self.target_heading - self.heading + 180) % 360 - 180
            if abs(diff) > 0.5:
                # Proportional rudder up to 20 degrees for normal course changes
                self.rudder_angle = max(-20.0, min(20.0, diff * 1.5))
            else:
                self.rudder_angle = 0.0
                self.heading = self.target_heading

        # Apply turn rate scaled by simulation time step (sim_dt)
        actual_turn = (self.rudder_angle / 35.0) * turn_rate_max_deg_per_sec * sim_dt
        self.heading = (self.heading + actual_turn) % 360

        # 2. Movement Logic (1 knot = 1 NM/hour = 1/3600 NM/second)
        distance_nm = (self.speed / 3600.0) * sim_dt
        distance_px = distance_nm * self.pixels_per_nm

        rad_heading = math.radians(self.heading)
        self.x += distance_px * math.sin(rad_heading)
        self.y -= distance_px * math.cos(rad_heading)

        # Record trail point
        current_pos = (int(self.x), int(self.y))
        if current_pos != self.trail[-1]:
            self.trail.append(current_pos)


if __name__ == "__main__":
    SCREEN_WIDTH = 800
    SCREEN_HEIGHT = 800

    loader = GeoDataLoader("coastline.geojson")
    raw_lines = loader.load()
    mapper = CoordinateMapper(loader.bounds, SCREEN_WIDTH, SCREEN_HEIGHT)
    pixel_lines = mapper.map_lines(raw_lines)

    display = DisplayManager(pixel_lines, SCREEN_WIDTH, SCREEN_HEIGHT)
    clock = pygame.time.Clock()

    # Load Simulation Data
    vdr_events = parse_vdr_data()
    TIME_SCALE = 100.0  # Runs 10x faster than real-time

    player_ship = None
    sim_time = 0.0
    event_index = 0

    running = True
    while running:
        dt = clock.tick(60) / 1000.0  # Real elapsed frame time in seconds
        sim_dt = dt * TIME_SCALE      # Simulation elapsed time in seconds

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and not player_ship:
                    mouse_x, mouse_y = event.pos

                    first_event = vdr_events[0]
                    initial_heading = first_event["heading"] if first_event["heading"] is not None else 285.0
                    initial_speed = first_event["speed"] if first_event["speed"] is not None else 15.8

                    player_ship = Ship(
                        mouse_x,
                        mouse_y,
                        pixels_per_nm=mapper.pixels_per_nm,
                        heading=initial_heading,
                        speed=initial_speed
                    )
                    player_ship.apply_command(first_event["heading"], first_event["rudder"], first_event["speed"])
                    sim_time = 0.0
                    event_index = 1

        if player_ship:
            sim_time += sim_dt

            while event_index < len(vdr_events) and sim_time >= vdr_events[event_index]["time"]:
                current_event = vdr_events[event_index]
                player_ship.apply_command(current_event["heading"], current_event["rudder"], current_event["speed"])
                event_index += 1

            player_ship.update(sim_dt)

        # Render loop
        display.clear()
        display.draw_coastline()
        display.draw_ship(player_ship)
        display.refresh()

    pygame.quit()
    sys.exit()