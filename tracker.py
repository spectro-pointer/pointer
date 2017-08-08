import cv2
import xmlrpclib
import time
import rpyc
import numpy as np
import matplotlib.pyplot as plt
from random import randint

from spectrometer import Spectrometer

class ErrorController:
    WIDTH = 640
    HEIGHT = 480
    ERROR_TOLERANCE = 1
    P_AZIMUTH = 5 # 30 is ~4px
    P_ELEVATION = 0.0003 # 0.0025 is ~3 px
    MAX_MULTIPLIER = 8

    def __init__(self, azimuth_controller, elevation_controller):
        self.azimuth_controller = azimuth_controller
        self.elevation_controller = elevation_controller

    def center(self, x, y):
        error_x = x - (self.WIDTH / 2)
        error_y = y - (self.HEIGHT / 2)

        if abs(error_x) <= self.ERROR_TOLERANCE and abs(error_y) <= self.ERROR_TOLERANCE:
            return True

        if abs(error_x) > self.ERROR_TOLERANCE:
            delta = min(abs(error_x), self.MAX_MULTIPLIER) * self.P_AZIMUTH
            if error_x <= 0:
                self.azimuth_controller.move_left(delta)
            else:
                self.azimuth_controller.move_right(delta)

        if abs(error_y) > self.ERROR_TOLERANCE:
            delta = min(abs(error_y), self.MAX_MULTIPLIER) * self.P_ELEVATION
            if error_y <= 0:
                elevation = elevation_controller.position() - delta
                # TODO Check elevation range
                self.elevation_controller.move_to(elevation)
            else:
                elevation = elevation_controller.position() + delta
                # TODO Check elevation range
                self.elevation_controller.move_to(elevation)

        return False

class LightState:
    def __init__(self):
        self.in_tracking = False
        self.tracked = False

class Busca:
    WIDTH = 640
    HEIGHT = 480
    MIN_LIGHT_AREA = 7

    def __init__(self, error_controller):
        self.error_controller = error_controller
        self.state = {}
        self.is_centered = False

    def update_state(self, lights):
        # Purge old state
        guids = [light["guid"] for light in lights]
        new_state = {}
        for guid in self.state:
            if guid in guids:
                new_state[guid] = self.state[guid]
        self.state = new_state

        # Initialize state for all new lights
        for light in lights:
            if light["guid"] not in self.state:
                self.state[light["guid"]] = LightState()

    def get_tracked_light(self, lights):
        for light in lights:
            light_state = self.state.get(light["guid"])

            if light_state != None and light_state.in_tracking:
                return light

        return None

    def get_new_light_to_track(self, lights):
        self.is_centered = False
        self.update_state(lights)

        right_most_light_to_track = None

        for light in lights:
            light_x = light["light"]["x"]
            light_state = self.state.get(light["guid"])

            if (light_x <= self.WIDTH / 2 and light["light"]["area"] >= self.MIN_LIGHT_AREA and
                    (not light_state.tracked) and
                    (right_most_light_to_track == None or light_x > right_most_light_to_track["light"]["x"])):
                right_most_light_to_track = light

        if right_most_light_to_track == None:
            return None

        light_state = self.state.get(right_most_light_to_track["guid"])
        light_state.in_tracking = True

        return right_most_light_to_track

    def center_tracked_light(self, lights):
        self.is_centered = False
        self.update_state(lights)

        tracked_light = self.get_tracked_light(lights)

        if tracked_light == None:
            print "  The tracked light disappeared"
            return True

        print "Tracking light %s at %d, %d" % (tracked_light["guid"], tracked_light["light"]["x"], tracked_light["light"]["y"])

        if not self.error_controller.center(tracked_light["light"]["x"], tracked_light["light"]["y"]):
            return False

        print "Centered on light %s at %d, %d" % (tracked_light["guid"], tracked_light["light"]["x"], tracked_light["light"]["y"])
        light_state = self.state.get(tracked_light["guid"])
        light_state.in_tracking = False
        light_state.tracked = True
        self.is_centered = True

        return True

class Coli:
    WIDTH = 190
    HEIGHT = 170
    FIBER_X = 75
    FIBER_Y = 77
    DX = 25
    DY = 4
    MIN_INTENSITY = 12000
    MAX_TRIALS = 30

    def __init__(self, coli_controller, elevation_controller):
        self.coli_controller = coli_controller
        self.elevation_controller = elevation_controller

    def colimate(self):
        for i in range(0, self.MAX_TRIALS):
            time.sleep(0.5)

            im_str = str(coli_controller.get_image())
            im = np.fromstring(im_str, dtype = np.uint8).reshape((self.HEIGHT, self.WIDTH, 3))
            im = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
            roi = im[self.FIBER_Y-self.DY:self.FIBER_Y+self.DY, self.FIBER_X-self.DX:self.FIBER_X+self.DX]
            intensity = cv2.sumElems(roi)[0]

            print "At elevation %f, measured colimation intensity of %d" % (elevation_controller.position(), intensity)

            if intensity >= self.MIN_INTENSITY:
                return True

            elevation_controller.move_to(elevation_controller.position() - 0.00025)

        return False

def scan(azimuth_controller, elevation_controller, lights_controller, busca, coli, spectrometer, elevation_steps):
    for elevation_step in range(0, elevation_steps):
        if elevation_step != 2:
            continue

        elevation = elevation_step*(1.0 / elevation_steps) + (1.0 / elevation_steps) / 2.0

        azimuth_controller.move_to(17250)

        while True:
            elevation_controller.move_to(elevation)

            print "@ elevation %f & azimuth %d" % (elevation_controller.position(), azimuth_controller.position())

            time.sleep(0.2)
            lights = lights_controller.get_lights()
            if busca.get_new_light_to_track(lights) == None:
                azimuth_controller.move_left(120)
                continue

            while not busca.center_tracked_light(lights):
                time.sleep(0.2)
                lights = lights_controller.get_lights()

            if busca.is_centered:
                print "  Final elevation %f & azimuth %d" % (elevation_controller.position(), azimuth_controller.position())

                time.sleep(5)

                if coli.colimate():
                    print "  Colimation succeeded, final coordinates: elevation %f & azimuth %d" % (elevation_controller.position(), azimuth_controller.position())

                    print "  Capturing spectrum..."
                    wavelengths = spectrometer.get_wavelengths()
                    wavelengths = [float(v) for v in wavelengths.split()]
                    spectrum = spectrometer.get_spectrum()
                    spectrum = [int(v) for v in spectrum.split()]
                    if spectrometer.get_current_status() == 'Success':
                        print "  The spectrum capture succeeded, showing it..."
                        plt.plot(wavelengths, spectrum)
                        plt.xlim(wavelengths[0], wavelengths[len(wavelengths) - 1])
                        plt.ylim(1000, 16500)
                        plt.ylabel('Intensity')
                        plt.xlabel('Wavelength')
                        plt.show()
                    else:
                        print "  The spectrum capture failed"
                else:
                    print "  Colimation failed"

MOTORES_IP = "127.0.0.1"
BUSCA_IP = "127.0.0.1"
COLI_IP = "127.0.0.1"
SPECTROMETER_IP = "127.0.0.1"
SPECTROMETER_INTEGRATION_TIME = 20

azimuth_controller = xmlrpclib.ServerProxy("http://" + MOTORES_IP + ":8000")
elevation_controller = xmlrpclib.ServerProxy("http://" + MOTORES_IP + ":8001")
lights_controller = xmlrpclib.ServerProxy("http://" + BUSCA_IP + ":8003")
coli_controller = xmlrpclib.ServerProxy("http://" + COLI_IP + ":8002")

spectrometer = Spectrometer(SPECTROMETER_IP, 1865)
spectrometer.set_integration(SPECTROMETER_INTEGRATION_TIME * 1e6)

busca = Busca(ErrorController(azimuth_controller, elevation_controller))
coli = Coli(coli_controller, elevation_controller)

scan(azimuth_controller, elevation_controller, lights_controller, busca, coli, spectrometer, elevation_steps = 4)