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