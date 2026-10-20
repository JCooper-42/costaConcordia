import json
import pygame
import sys

class GeoDataLoader:
    def __init__(self, filepath):
        self.filepath = filepath
        self.lines = []
        self.min_lon, self.max_lon = float('inf'), float('-inf')
        self.min_lat, self.max_lat = float('inf'), float('-inf')

    def load(self):
        with open(self.filepath, 'r') as f:
            data = json.load(f)
            
        for feature in data.get('features', []):
            geom = feature.get('geometry', {})
            geom_type = geom.get('type')
            coords = geom.get('coordinates', [])

            # Extract continuous lines from both types
            if geom_type == 'LineString':
                self.lines.append(coords)
                self._update_bounds(coords)
            elif geom_type == 'Polygon':
                for ring in coords:
                    self.lines.append(ring)
                    self._update_bounds(ring)
                    
        return self.lines

    def _update_bounds(self, points):
        for lon, lat in points:
            if lon < self.min_lon: self.min_lon = lon
            if lon > self.max_lon: self.max_lon = lon
            if lat < self.min_lat: self.min_lat = lat
            if lat > self.max_lat: self.max_lat = lat

class CoordinateMapper:
    def __init__(self, min_lon, max_lon, min_lat, max_lat, width, height, padding=20):
        self.min_lon = min_lon
        self.max_lon = max_lon
        self.min_lat = min_lat
        self.max_lat = max_lat
        
        # Calculate scaling factors while maintaining aspect ratio
        lon_range = max_lon - min_lon
        lat_range = max_lat - min_lat
        
        usable_width = width - (padding * 2)
        usable_height = height - (padding * 2)
        
        scale_x = usable_width / lon_range if lon_range > 0 else 1
        scale_y = usable_height / lat_range if lat_range > 0 else 1
        self.scale = min(scale_x, scale_y)
        
        # Center the map
        self.offset_x = (width - (lon_range * self.scale)) / 2
        self.offset_y = (height - (lat_range * self.scale)) / 2

    def map_point(self, lon, lat):
        x = (lon - self.min_lon) * self.scale + self.offset_x
        # Invert Y axis because screen Y goes down, but latitude goes up
        y = self.offset_y + (self.max_lat - self.min_lat) * self.scale - ((lat - self.min_lat) * self.scale)
        return int(x), int(y)

class Boat:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)
        self.speed = 3.0
        self.size = 6
        self.color = (255, 0, 0) # Red boat

    def update(self, keys):
        if keys[pygame.K_LEFT]:
            self.x -= self.speed
        if keys[pygame.K_RIGHT]:
            self.x += self.speed
        if keys[pygame.K_UP]:
            self.y -= self.speed
        if keys[pygame.K_DOWN]:
            self.y += self.speed

    def draw(self, surface):
        rect = pygame.Rect(self.x - self.size//2, self.y - self.size//2, self.size, self.size)
        pygame.draw.rect(surface, self.color, rect)

class Simulation:
    def __init__(self, geojson_path, width=800, height=800):
        pygame.init()
        self.screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption("Coastline Simulator")
        self.clock = pygame.time.Clock()

        # Load Map Data
        self.loader = GeoDataLoader(geojson_path)
        self.raw_lines = self.loader.load()
        
        # Setup coordinates
        self.mapper = CoordinateMapper(
            self.loader.min_lon, self.loader.max_lon,
            self.loader.min_lat, self.loader.max_lat,
            width, height
        )
        
        # Convert all geo-coordinates to screen coordinates once for efficiency
        self.screen_lines = []
        for line in self.raw_lines:
            screen_line = [self.mapper.map_point(lon, lat) for lon, lat in line]
            self.screen_lines.append(screen_line)

        # Initialize boat in the center of the screen
        self.boat = Boat(width // 2, height // 2)

    def run(self):
        running = True
        while running:
            # 1. Handle Events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            # 2. Update Logic
            keys = pygame.key.get_pressed()
            self.boat.update(keys)

            # 3. Rendersudo
            self.screen.fill((0, 0, 0)) # Black background
            
            # Draw coastline
            for line in self.screen_lines:
                if len(line) > 1:
                    pygame.draw.lines(self.screen, (255, 255, 255), False, line, 2)
            
            # Draw boat
            self.boat.draw(self.screen)

            pygame.display.flip()
            self.clock.tick(60) # 60 FPS

        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    sim = Simulation("coastline.geojson")
    sim.run()