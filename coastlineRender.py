import json
import sys
import pygame


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
    """Converts geographic GPS coordinates (Lon/Lat) to pixel screen coordinates (X, Y)."""

    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        width: int,
        height: int,
        padding: int = 20,
    ):
        self.min_lon, self.max_lon, self.min_lat, self.max_lat = bounds

        lon_range = self.max_lon - self.min_lon
        lat_range = self.max_lat - self.min_lat

        usable_width = width - (padding * 2)
        usable_height = height - (padding * 10)

        scale_x = usable_width / lon_range if lon_range > 0 else 1
        scale_y = usable_height / lat_range if lat_range > 0 else 1
        self.scale = min(scale_x, scale_y)

        self.offset_x = (width - (lon_range * self.scale)) / 2
        self.offset_y = (height - (lat_range * self.scale)) / 2

    def map_point(self, lon: float, lat: float) -> tuple[int, int]:
        x = (lon - self.min_lon) * self.scale + self.offset_x
        y = (
            self.offset_y
            + (self.max_lat - self.min_lat) * self.scale
            - ((lat - self.min_lat) * self.scale)
        )
        return int(x), int(y)

    def map_lines(self, lines: list) -> list:
        """Batch transforms raw GPS coordinate lines into pixel lines."""
        return [
            [self.map_point(lon, lat) for lon, lat in line]
            for line in lines
        ]


class DisplayManager:
    """Manages Pygame window initialization and vector coastline rendering."""

    BG_COLOR = (0, 0, 0)
    COAST_COLOR = (255, 255, 255)

    def __init__(
        self,
        screen_lines: list,
        width: int = 800,
        height: int = 800,
        title: str = "Costa Concordia Simulator",
    ):
        pygame.init()
        self.screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption(title)
        self.screen_lines = screen_lines

    def clear(self):
        self.screen.fill(self.BG_COLOR)

    def draw_coastline(self, line_width: int = 2):
        for line in self.screen_lines:
            if len(line) > 1:
                pygame.draw.lines(
                    self.screen, self.COAST_COLOR, False, line, line_width
                )

    def refresh(self):
        pygame.display.flip()

class ship:

    def __init__(self, draft, beam, LOA, heading, speed):
        self.draft = draft
        self.beam = beam
        self.LOA = LOA
        self.heading = heading
        self.speed = speed
        self.isAground = False

    def turnPort(self, angle):
        self.heading -= angle
        if self.heading < 0:
            self.heading += 360

    def turnStarboard(self, angle):
        self.heading += angle
        if self.heading >= 360:
            self.heading -= 360

    def accelerate(self, deltaSpeed):
        self.speed += deltaSpeed
        if self.speed < 0:
            self.speed = 0

    def decelerate(self, deltaSpeed):
        self.speed -= deltaSpeed
        if self.speed < 0:
            self.speed = 0

if __name__ == "__main__":
    SCREEN_WIDTH = 800
    SCREEN_HEIGHT = 800

    # 1. Load raw data once
    loader = GeoDataLoader("coastline.geojson")
    raw_lines = loader.load()

    # 2. Map raw data to screen pixels once
    mapper = CoordinateMapper(loader.bounds, SCREEN_WIDTH, SCREEN_HEIGHT)
    pixel_lines = mapper.map_lines(raw_lines)

    # 3. Pass mapped render data directly to display manager
    display = DisplayManager(pixel_lines, SCREEN_WIDTH, SCREEN_HEIGHT)
    clock = pygame.time.Clock()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        display.clear()
        display.draw_coastline()
        display.refresh()
        clock.tick(60)

    pygame.quit()
    sys.exit()