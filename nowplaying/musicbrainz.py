#!/usr/bin/env python3
# pylint: disable=invalid-name
''' support for musicbrainz '''

import os
import sys

import logging
import logging.config
import logging.handlers

import musicbrainzngs

import nowplaying.bootstrap
import nowplaying.config
import nowplaying.version


class MusicBrainzHelper():
    ''' handler for NowPlaying '''

    def __init__(self, config=None):
        logging.getLogger('musicbrainzngs').setLevel(logging.CRITICAL + 1)
        if config:
            self.config = config
        else:
            self.config = nowplaying.config.ConfigFile()

        self.emailaddressset = False

    def _setemail(self):
        ''' make sure the musicbrainz fetch has an email address set
            according to their requirements '''
        if not self.emailaddressset:
            emailaddress = self.config.cparser.value(
                'musicbrainz/emailaddress')

            if not emailaddress:
                emailaddress = 'aw@effectivemachines.com'

            musicbrainzngs.set_useragent(
                'whats-now-playing',
                nowplaying.version.get_versions()['version'], emailaddress)
            self.emailaddressset = True

    def recognize(self, metadata):
        ''' fill in any blanks from musicbrainz '''

        if not self.config.cparser.value(
                'musicbrainz/enabled', type=bool) or self.config.cparser.value(
                    'control/beam', type=bool):
            return None

        addmeta = {}

        if metadata.get('musicbrainzrecordingid'):
            logging.debug('Preprocessing with musicbrainz recordingid')
            addmeta = self.recordingid(metadata['musicbrainzrecordingid'])
        elif metadata.get('isrc'):
            logging.debug('Preprocessing with musicbrainz isrc')
            addmeta = self.isrc(metadata['isrc'])
        elif metadata.get('musicbrainzartistid'):
            logging.debug('Preprocessing with musicbrainz artistid')
            addmeta = self.artistids(metadata['musicbrainzartistid'])
        return addmeta

    def isrc(self, isrclist):
        ''' lookup musicbrainz information based upon isrc '''
        if not self.config.cparser.value(
                'musicbrainz/enabled', type=bool) or self.config.cparser.value(
                    'control/beam', type=bool):
            return None

        self._setemail()

        for isrc in isrclist:
            try:
                mbdata = musicbrainzngs.get_recordings_by_isrc(
                    isrc, includes=['releases'], release_status=['official'])
            except Exception:  # pylint: disable=broad-except
                pass

        if not mbdata:
            for isrc in isrclist:
                try:
                    mbdata = musicbrainzngs.get_recordings_by_isrc(
                        isrc, includes=['releases'])
                except Exception:  # pylint: disable=broad-except
                    logging.info('musicbrainz cannot find ISRC %s', isrc)

        if 'isrc' not in mbdata or 'recording-list' not in mbdata['isrc']:
            return None

        recordinglist = sorted(mbdata['isrc']['recording-list'],
                               key=lambda k: k['release-count'],
                               reverse=True)
        return self.recordingid(recordinglist[0]['id'])

    def recordingid(self, recordingid):  # pylint: disable=too-many-branches, too-many-return-statements, too-many-statements
        ''' lookup the musicbrainz information based upon recording id '''
        if not self.config.cparser.value(
                'musicbrainz/enabled', type=bool) or self.config.cparser.value(
                    'control/beam', type=bool):
            return None

        self._setemail()

        def read_label(releasedata):
            if 'label-info-list' not in releasedata:
                return None

            for labelinfo in releasedata['label-info-list']:
                if 'label' not in labelinfo:
                    continue

                if 'type' not in labelinfo['label']:
                    continue

                if 'name' in labelinfo['label']:
                    return labelinfo['label']['name']

            return None

        def releaselookup_noartist(recordingid):
            mbdata = None

            self._setemail()

            try:
                mbdata = musicbrainzngs.browse_releases(
                    recording=recordingid,
                    includes=['labels', 'artist-credits'],
                    release_status=['official'])
            except Exception as error:  # pylint: disable=broad-except
                logging.debug('MusicBrainz threw an error: %s', error)
                return None

            if 'release-count' not in mbdata or mbdata['release-count'] == 0:
                try:
                    mbdata = musicbrainzngs.browse_releases(
                        recording=recordingid,
                        includes=['labels', 'artist-credits'])
                except Exception:  # pylint: disable=broad-except
                    logging.debug('MusicBrainz threw an error: %s', error)
                    return None
            return mbdata

        def _pickarelease(newdata, mbdata):
            namedartist = []
            variousartist = []
            for release in mbdata['release-list']:
                if len(newdata['musicbrainzartistid']) > 1 and newdata.get(
                        'artist'
                ) and release['artist-credit-phrase'] in newdata['artist']:
                    namedartist.append(release)
                elif 'artist' in newdata and release[
                        'artist-credit-phrase'] == newdata['artist']:
                    namedartist.append(release)
                elif release['artist-credit-phrase'] == 'Various Artists':
                    variousartist.append(release)

            if not namedartist:
                return variousartist

            return namedartist

        newdata = {'musicbrainzrecordingid': recordingid}
        try:
            mbdata = musicbrainzngs.get_recording_by_id(recordingid,
                                                        includes=['artists'])
        except Exception as error:  # pylint: disable=broad-except
            logging.error('MusicBrainz does not know recording id %s: %s',
                          recordingid, error)
            return None

        if 'recording' in mbdata and 'title' in mbdata['recording']:
            newdata['title'] = mbdata['recording']['title']
        if 'recording' in mbdata and 'artist-credit-phrase' in mbdata[
                'recording']:
            newdata['artist'] = mbdata['recording']['artist-credit-phrase']
            for artist in mbdata['recording']['artist-credit']:
                if not isinstance(artist, dict):
                    continue
                if not newdata.get('musicbrainzartistid'):
                    newdata['musicbrainzartistid'] = []
                newdata['musicbrainzartistid'].append(artist['artist']['id'])

        mbdata = releaselookup_noartist(recordingid)

        if not mbdata or 'release-count' not in mbdata or mbdata[
                'release-count'] == 0:
            return newdata

        mbdata = _pickarelease(newdata, mbdata)
        if not mbdata:
            return None

        release = mbdata[0]
        if 'title' in release:
            newdata['album'] = release['title']
        if 'date' in release:
            newdata['date'] = release['date']
        label = read_label(release)
        if label:
            newdata['label'] = label

        if 'cover-art-archive' in release and 'artwork' in release[
                'cover-art-archive'] and release['cover-art-archive'][
                    'artwork']:
            try:
                newdata['coverimageraw'] = musicbrainzngs.get_image(
                    release['id'], 'front')
            except Exception as error:  # pylint: disable=broad-except
                logging.error('Failed to get cover art: %s', error)

        newdata['artistwebsites'] = self._websites(
            newdata['musicbrainzartistid'])
        return newdata

    def artistids(self, idlist):
        ''' add data available via musicbrainz artist ids '''

        self._setemail()

        if not self.config.cparser.value(
                'musicbrainz/enabled', type=bool) or self.config.cparser.value(
                    'control/beam', type=bool):
            return None

        return {'artistwebsites': self._websites(idlist)}

    def _websites(self, idlist):
        if not self.config.cparser.value('acoustidmb/websites',
                                         type=bool) or not idlist:
            return None

        sitelist = []
        for artistid in idlist:
            if self.config.cparser.value('acoustidmb/musicbrainz', type=bool):
                sitelist.append(f'https://musicbrainz.org/artist/{artistid}')
            try:
                webdata = musicbrainzngs.get_artist_by_id(
                    artistid, includes=['url-rels'])
            except Exception as error:  # pylint: disable=broad-except
                logging.error('MusicBrainz does not know artistid id %s: %s',
                              artistid, error)
                return None

            if not webdata.get('artist') or not webdata['artist'].get(
                    'url-relation-list'):
                continue

            convdict = {
                'bandcamp': 'bandcamp',
                'official homepage': 'homepage',
                'last.fm': 'lastfm',
                'discogs': 'discogs',
            }

            for urlrel in webdata['artist']['url-relation-list']:
                logging.debug('checking %s', urlrel['type'])
                for src, dest in convdict.items():
                    if urlrel['type'] == src and self.config.cparser.value(
                            f'acoustidmb/{dest}', type=bool):
                        sitelist.append(urlrel['target'])
                        logging.debug('placed %s', dest)

        return sitelist

    def providerinfo(self):  # pylint: disable=no-self-use
        ''' return list of what is provided by this recognition system '''
        return [
            'album',
            'artist',
            'artistwebsites',
            'coverimageraw',
            'date',
            'label',
            'title',
        ]


def main():
    ''' integration test '''
    isrc = sys.argv[1]

    bundledir = os.path.abspath(os.path.dirname(__file__))
    logging.basicConfig(level=logging.DEBUG)
    nowplaying.bootstrap.set_qt_names()
    # need to make sure config is initialized with something
    nowplaying.config.ConfigFile(bundledir=bundledir)
    musicbrainz = MusicBrainzHelper(config=nowplaying.config.ConfigFile(
        bundledir=bundledir))
    metadata = musicbrainz.recordingid(isrc)
    if not metadata:
        print('No information')
        sys.exit(1)

    if 'coverimageraw' in metadata:
        print('got an image')
        del metadata['coverimageraw']
    print(metadata)


if __name__ == "__main__":
    main()
