#!/usr/bin/env python3
''' A _very_ simple and incomplete parser for Serato Live session files '''

import logging
import os

from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import PatternMatchingEventHandler

from PySide6.QtCore import QDir  # pylint: disable=no-name-in-module
from PySide6.QtWidgets import QFileDialog  # pylint: disable=no-name-in-module

from nowplaying.inputs import InputPlugin
from nowplaying.exceptions import PluginVerifyError
import nowplaying.utils

# https://datatracker.ietf.org/doc/html/rfc8216


class Plugin(InputPlugin):
    ''' handler for NowPlaying '''

    metadata = {'artist': None, 'title': None, 'filename': None}

    def __init__(self, config=None, m3udir=None, qsettings=None):
        super().__init__(config=config, qsettings=qsettings)
        if m3udir and os.path.exists(m3udir):
            self.m3udir = m3udir
        else:
            self.m3udir = None

        self.mixmode = "newest"
        self.event_handler = None
        self.observer = None
        self._reset_meta()

    def install(self):
        ''' locate Virtual DJ '''
        return False

    @staticmethod
    def _reset_meta():
        Plugin.metadata = {'artist': None, 'title': None, 'filename': None}

    async def setup_watcher(self, configkey):
        ''' set up a custom watch on the m3u dir so meta info
            can update on change'''

        m3udir = self.config.cparser.value(configkey)
        if not self.m3udir or self.m3udir != m3udir:
            await self.stop()

        if self.observer:
            return

        self.m3udir = m3udir
        if not self.m3udir:
            logging.error('M3U Directory Path does not exist: %s', self.m3udir)
            return

        logging.info('Watching for changes on %s', self.m3udir)
        #if len(os.listdir(self.m3udir)) < 1:
        #    pathlib.Path(os.path.join(self.m3udir, 'empty.m3u')).touch()

        self.event_handler = PatternMatchingEventHandler(
            patterns=['*.m3u', '*.m3u8'],
            ignore_patterns=['.DS_Store'],
            ignore_directories=True,
            case_sensitive=False)
        self.event_handler.on_modified = self._read_track
        self.event_handler.on_created = self._read_track

        if self.config.cparser.value('quirks/pollingobserver', type=bool):
            logging.debug('Using polling observer')
            self.observer = PollingObserver(timeout=5)
        else:
            logging.debug('Using fsevent observer')
            self.observer = Observer()
        self.observer.schedule(self.event_handler,
                               self.m3udir,
                               recursive=False)
        self.observer.start()

    def _verify_file(self, m3ufilename, filestring):
        found = None
        for encoding in ['utf-8', 'ascii', 'cp1252', 'utf-16']:
            logging.debug(filestring)
            try:
                location = filestring.decode(encoding)
            except UnicodeDecodeError:
                logging.debug('Definitely not %s', encoding)
                continue
            if location[0] == '#':
                break
            location = location.replace('file://', '')
            location = nowplaying.utils.songpathsubst(self.config, location)
            if os.path.exists(location):
                found = location
                break

            dirpath = os.path.dirname(m3ufilename)
            attempt2 = os.path.join(dirpath, location)
            attempt2 = nowplaying.utils.songpathsubst(self.config, attempt2)
            if os.path.exists(attempt2):
                found = attempt2
                break
        return found

    @staticmethod
    def _read_track_default(filename):
        content = None
        with open(filename, 'rb') as m3ufh:
            while True:
                newline = m3ufh.readline()
                if not newline:
                    break
                newline = newline.rstrip()
                if not newline or newline[0] == '#':
                    continue
                content = newline
        return content

    def _read_full_file(self, filename):
        content = []
        with open(filename, 'rb') as m3ufh:
            while True:
                newline = m3ufh.readline()
                if not newline:
                    break
                newline = newline.rstrip()
                if not newline:
                    continue
                if audiofilename := self._verify_file(filename, newline):
                    content.append(audiofilename)
        return content

    def _read_track(self, event):

        if event.is_directory:
            return

        filename = event.src_path
        logging.debug('event type: %s, syn: %s, path: %s', event.event_type,
                      event.is_synthetic, filename)

        # file is empty so ignore it
        if os.stat(filename).st_size == 0:
            logging.debug('%s is empty, ignoring for now.', filename)
            self._reset_meta()
            return

        content = self._read_track_default(filename)

        logging.debug('attempting to parse \'%s\' with various encodings',
                      content)

        if not content:
            self._reset_meta()
            return

        audiofilename = self._verify_file(filename, content)

        if not audiofilename:
            self._reset_meta()
            return

        newmeta = {'filename': audiofilename}
        Plugin.metadata = newmeta

    async def start(self):
        ''' setup the watcher to run in a separate thread '''
        await self.setup_watcher('m3u/directory')

    async def getplayingtrack(self):
        ''' wrapper to call getplayingtrack '''

        # just in case called without calling start...
        await self.start()
        return Plugin.metadata

    async def getrandomtrack(self, playlist):
        ''' not supported '''
        return None

    def defaults(self, qsettings):  #pylint: disable=no-self-use
        pass

    def validmixmodes(self):  #pylint: disable=no-self-use
        ''' let the UI know which modes are valid '''
        return ['newest']

    def setmixmode(self, mixmode):  #pylint: disable=no-self-use
        ''' set the mixmode '''
        return 'newest'

    def getmixmode(self):  #pylint: disable=no-self-use
        ''' get the mixmode '''
        return 'newest'

    async def stop(self):
        ''' stop the m3u plugin '''
        self._reset_meta()
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None

    def on_m3u_dir_button(self):
        ''' filename button clicked action'''
        if self.qwidget.dir_lineedit.text():
            startdir = self.qwidget.dir_lineedit.text()
        else:
            startdir = QDir.homePath()
        if dirname := QFileDialog.getExistingDirectory(self.qwidget,
                                                       'Select directory',
                                                       startdir):
            self.qwidget.dir_lineedit.setText(dirname)

    def connect_settingsui(self, qwidget, uihelp):
        ''' connect m3u button to filename picker'''
        self.qwidget = qwidget
        self.uihelp = uihelp
        qwidget.dir_button.clicked.connect(self.on_m3u_dir_button)

    def load_settingsui(self, qwidget):
        ''' draw the plugin's settings page '''
        qwidget.dir_lineedit.setText(
            self.config.cparser.value('m3u/directory'))

    def verify_settingsui(self, qwidget):
        ''' no verification to do '''
        if not os.path.exists(qwidget.dir_lineedit.text()):
            raise PluginVerifyError(r'm3u directory must exist.')

    def save_settingsui(self, qwidget):
        ''' take the settings page and save it '''
        configdir = qwidget.dir_lineedit.text()
        self.config.cparser.setValue('m3u/directory', configdir)

    def desc_settingsui(self, qwidget):
        ''' description '''
        qwidget.setText('M3U is a generic playlist format that is supported '
                        'by a wide variety of tools, including Virtual DJ.')
