import argparse
import datetime
import os
import re
import sys
import time
import yaml

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.select import Select
from selenium.webdriver.firefox.options import Options

URL = 'http://applications.maxrelax.ro/Web/MaxRelaxSubscribe/Pages/LoginPage.aspx'
USER = 'user'
PASSWORD = 'password'
SUBSCRIBE_URL = 'http://applications.maxrelax.ro/Web/MaxRelaxSubscribe/Pages/Subscribe.aspx'
UI_ACTION_DELAY_SEC = 1

SCHEDULE_START_HOUR = 10
SCHEDULE_START_MIN = 0

SCHEDULE_SLOT_INTERVAL = 15

SCHEDULE_STOP_HOUR = 16
SCHEDULE_STOP_MIN = 15

RETRY_INTERVAL_MIN = 5
RETRY_COUNT_MAX = 4


def time_to_slot(when):
    h, m = map(int, when.split(':'))
    m = min(59, max(0, m))
    h = min(23, max(0, h))
    t = (h * 60 + m) - (SCHEDULE_START_HOUR * 60 + SCHEDULE_START_MIN)
    t = max(t, 0)
    t = min(t, (SCHEDULE_STOP_HOUR * 60 + SCHEDULE_STOP_MIN - SCHEDULE_SLOT_INTERVAL))
    return t // SCHEDULE_SLOT_INTERVAL


def slot_to_time(slot):
    slot = max(slot, 0)
    t = slot * SCHEDULE_SLOT_INTERVAL + (SCHEDULE_START_HOUR * 60 + SCHEDULE_START_MIN)
    t = min(t, SCHEDULE_STOP_HOUR * 60 + SCHEDULE_STOP_MIN - SCHEDULE_SLOT_INTERVAL)
    return '{}:{:02d}'.format(t // 60, t % 60)


SCHEDULE_ALL_SLOTS = range(0, time_to_slot('{}:{}'.format(SCHEDULE_STOP_HOUR, SCHEDULE_STOP_MIN)))


def merge(a, b):
    i = 0
    while i < len(a) and i < len(b):
        yield a[i]
        yield b[i]
        i += 1
    while i < len(a):
        yield a[i]
        i += 1
    while i < len(b):
        yield b[i]
        i += 1


class SlotUnavailable(Exception):

    def __init__(self, slot):
        super(SlotUnavailable, self).__init__('Slot is unavailable: {}'.format(slot))


class PersonNotFound(Exception):

    def __init__(self, who):
        super(PersonNotFound, self).__init__('Person not found: {}'.format(who))


class MissingReservationCode(Exception):

    def __init__(self, text):
        super(MissingReservationCode, self).__init__('Missing reservation code: {}'.format(text))


class PersonAlreadyHasReservation(Exception):

    def __init__(self, who):
        super(PersonAlreadyHasReservation, self).__init__('Person already has a reservation: {}'.format(who))


class ReservationsNotOpen(Exception):

    def __init__(self):
        super(ReservationsNotOpen, self).__init__('Reservations are not open')


class Schedule(object):

    def __init__(self, driver):
        self.driver = driver

    def login(self):
        self.driver.get(URL)
        elem = self.driver.find_element_by_id('txtUserName')
        elem.clear()
        elem.send_keys(USER)
        elem = self.driver.find_element_by_id('txtPassword')
        elem.clear()
        elem.send_keys(PASSWORD)
        elem = self.driver.find_element_by_id('btnLogin')
        elem.send_keys(Keys.RETURN)
        time.sleep(UI_ACTION_DELAY_SEC)
        self.driver.get(SUBSCRIBE_URL)
        try:
            self.driver.find_element(By.XPATH, "//a[contains(.,'Subscribe')]").click()
            time.sleep(UI_ACTION_DELAY_SEC)
        except NoSuchElementException:
            raise ReservationsNotOpen()
        self._scan_gaps()

    def logout(self):
        elem = self.driver.find_element_by_id('btnLogout')
        elem.send_keys(Keys.RETURN)
        time.sleep(UI_ACTION_DELAY_SEC)
        self.driver.get(SUBSCRIBE_URL)

    def _scan_gaps(self):
        self.slot_to_id = {}
        for row in self.driver.find_elements(By.XPATH, "//span[contains(@id,'MainContent_grdSessions2Users2Persons_lblPerson_')]/../.."):
            cells = row.find_elements_by_tag_name("td")
            time = cells[0].text.strip()
            _id = cells[1].find_elements_by_tag_name("span")[0].get_attribute("id").split("_")[-1]
            self.slot_to_id[time_to_slot(time)] = _id

    def reserve_slot(self, who, slot):
        for row in self.driver.find_elements(By.XPATH, "//a[contains(.,'Cancel')]/../.."):
            cells = row.find_elements_by_tag_name("td")
            name = cells[1].text.strip()
            if name == who:
                raise PersonAlreadyHasReservation(who)
        try:
            _id = self.slot_to_id[slot]
        except KeyError:
            raise SlotUnavailable(slot)
        try:
            subscribe = self.driver.find_element_by_id(
                'MainContent_grdSessions2Users2Persons_btnSubscribe_{}'.format(_id))
            if subscribe.text.lower().strip() != 'subscribe':
                raise SlotUnavailable(slot)
            subscribe.click()
            time.sleep(UI_ACTION_DELAY_SEC)
        except NoSuchElementException:
            raise SlotUnavailable(slot)
        try:
            person = Select(self.driver.find_element_by_id(
                'MainContent_grdSessions2Users2Persons_ddlPerson_{}'.format(_id)))
            person.select_by_visible_text(who)
            time.sleep(UI_ACTION_DELAY_SEC)
        except NoSuchElementException:
            raise PersonNotFound(who)
        try:
            ok_button = self.driver.find_element_by_id(
                'MainContent_grdSessions2Users2Persons_btnSelectUser_{}'.format(_id))
            ok_button.click()
            time.sleep(UI_ACTION_DELAY_SEC)
        except NoSuchElementException:
            raise SlotUnavailable(slot)
        try:
            reservation = self.driver.find_element_by_id(
                'MainContent_grdSessions2Users2Persons_lblPerson_{}'.format(_id))
            match = re.search('cancelation code: (?P<code>[0-9]+)', reservation.text)
            if not match:
                raise MissingReservationCode(reservation.text)
            return match.group('code')
        except NoSuchElementException:
            try:
                reason = self.driver.find_element_by_id(
                    'MainContent_grdSessions2Users2Persons_lblMessage_{}'.format(_id))
                if 'exceeded' in reason.text:
                    raise PersonAlreadyHasReservation(who)
            except NoSuchElementException:
                pass
            raise MissingReservationCode('message not found')

    def reserve(self, reservations, retry_credit=RETRY_COUNT_MAX, retry_interval=RETRY_INTERVAL_MIN):
        retry_reservations = reservations
        permanent_failures = []
        ok_reservations = []
        while retry_credit > 0:
            if not len(reservations):
                return ok_reservations, permanent_failures
            reservations = retry_reservations
            retry_reservations = []
            for who, when in reservations:
                self.login()
                slot = time_to_slot(when)
                later_slots = SCHEDULE_ALL_SLOTS[slot + 1::1]
                earlier_slots = SCHEDULE_ALL_SLOTS[slot - 1::-1]
                for slot in [slot] + list(merge(later_slots, earlier_slots)):
                    try:
                        code = self.reserve_slot(who, slot)
                        ok_reservations.append((who, slot_to_time(slot), code))
                        break
                    except SlotUnavailable:
                        pass
                    except Exception as e:
                        permanent_failures.append((who, when, str(e)))
                        break
                else:
                    # retry later
                    retry_reservations.append((who, when))
                self.logout()
            if len(retry_reservations):
                # retry later
                time.sleep(60 * RETRY_INTERVAL_MIN)
                retry_credit -= 1
        permanent_failures.extend(
            [(who, when, 'Failed to reserve')
             for (who, when) in retry_reservations])
        return ok_reservations, permanent_failures


def make_reservations(driver, reservations):
    print('===', datetime.datetime.now())
    ok, failed = Schedule(driver).reserve(reservations)
    for who, when, code in ok:
        print('Reserved: name={}, time={}, cancelation_code={}'.format(who, when, code))
    for who, when, reason in failed:
        print('Failed to reserve: name={}, time={}, reason={}'.format(who, when, reason))


def main():
    global USER
    global PASSWORD
    parser = argparse.ArgumentParser(description='MaxRelax reservations')
    parser.add_argument('reservations', metavar='R',
                        nargs='*', help='reservation as name')
    parser.add_argument('--config', required=False,
                        default=os.path.join(
                            os.path.expanduser('~'),
                            '.maxrelax'),
                        help='configuration file')
    args = parser.parse_args()
    reservations = []
    for reservation in args.reservations:
        fragments = reservation.split()
        who = ' '.join(fragments[:-1])
        when = fragments[-1]
        reservations.append((who, when))
    try:
        with open(args.config) as config_file:
            config = yaml.load(config_file.read(),
                               Loader=yaml.SafeLoader)
            if not reservations:
                reservations = [
                    (who, when) for who, when
                    in config['reservations'].items()]
            USER = config['credentials']['user']
            PASSWORD = config['credentials']['password']
    except Exception as e:
        print('ERROR:', str(e))
        sys.exit(1)
    options = Options()
    options.set_headless(headless=True)
    driver = webdriver.Firefox(firefox_options=options)
    try:
        retcode = make_reservations(driver, reservations)
    except ReservationsNotOpen as e:
        print('ERROR:', str(e))
        retcode = 1
    finally:
        driver.quit()
    print()
    sys.exit(retcode)


if __name__ == '__main__':
    main()

