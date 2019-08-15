# Export to CEDDN

from collections import OrderedDict
import json
from os import SEEK_SET, SEEK_CUR, SEEK_END
from os.path import exists, join
from platform import system
import re
import requests
import sys
import uuid

import Tkinter as tk
from ttkHyperlinkLabel import HyperlinkLabel
import myNotebook as nb

if sys.platform != 'win32':
    from fcntl import lockf, LOCK_EX, LOCK_NB

if __debug__:
    from traceback import print_exc

from config import applongname, appversion, config
from companion import category_map


this = sys.modules[__name__]	# For holding module globals

# Track location to add to Journal events
this.systemaddress = None
this.coordinates = None
this.planet = None



class CEDDN:

    SERVER = 'https://ceddn.canonn.tech:4430'
    UPLOAD = '%s/upload/' % SERVER
    REPLAYPERIOD = 400	# Roughly two messages per second, accounting for send delays [ms]
    REPLAYFLUSH = 20	# Update log on disk roughly every 10 seconds
    TIMEOUT= 10	# requests timeout
    MODULE_RE = re.compile('^Hpt_|^Int_|Armour_', re.IGNORECASE)
    CANONICALISE_RE = re.compile(r'\$(.+)_name;')

    def __init__(self, parent):
        self.parent = parent
        self.session = requests.Session()
        self.replayfile = None	# For delayed messages
        self.replaylog = []

    def load(self):
        # Try to obtain exclusive access to the journal cache
        filename = join(config.app_dir, 'replay.jsonl')
        try:
            try:
                # Try to open existing file
                self.replayfile = open(filename, 'r+')
            except:
                if exists(filename):
                    raise	# Couldn't open existing file
                else:
                    self.replayfile = open(filename, 'w+')	# Create file
            if sys.platform != 'win32':	# open for writing is automatically exclusive on Windows
                lockf(self.replayfile, LOCK_EX|LOCK_NB)
        except:
            if __debug__: print_exc()
            if self.replayfile:
                self.replayfile.close()
            self.replayfile = None
            return False
        self.replaylog = [line.strip() for line in self.replayfile]
        return True

    def flush(self):
        self.replayfile.seek(0, SEEK_SET)
        self.replayfile.truncate()
        for line in self.replaylog:
            self.replayfile.write('%s\n' % line)
        self.replayfile.flush()

    def close(self):
        if self.replayfile:
            self.replayfile.close()
        self.replayfile = None

    def send(self, cmdr, msg):
        if config.getint('anonymous'):
            uploaderID = config.get('uploaderID')
            if not uploaderID:
                uploaderID = uuid.uuid4().hex
                config.set('uploaderID', uploaderID)
        else:
            uploaderID = cmdr.encode('utf-8')

        msg = OrderedDict([
            ('$schemaRef', msg['$schemaRef']),
            ('header',     OrderedDict([
                ('softwareName',    '%s [%s]' % (applongname, sys.platform=='darwin' and "Mac OS" or system())),
                ('softwareVersion', appversion),
                ('uploaderID',      uploaderID),
            ])),
            ('message',    msg['message']),
        ])

        r = self.session.post(self.UPLOAD, data=json.dumps(msg), timeout=self.TIMEOUT)
        if __debug__ and r.status_code != requests.codes.ok:
            print 'Status\t%s'  % r.status_code
            print 'URL\t%s'  % r.url
            print 'Headers\t%s' % r.headers
            print ('Content:\n%s' % r.text).encode('utf-8')
        r.raise_for_status()

    def sendreplay(self):
        if not self.replayfile:
            return	# Probably closing app

        status = self.parent.children['status']

        if not self.replaylog:
            status['text'] = ''
            return

        if len(self.replaylog) == 1:
            status['text'] = _('Sending data to CEDDN...')
        else:
            status['text'] = '%s [%d]' % (_('Sending data to CEDDN...').replace('...',''), len(self.replaylog))
        self.parent.update_idletasks()
        try:
            cmdr, msg = json.loads(self.replaylog[0], object_pairs_hook=OrderedDict)
        except:
            # Couldn't decode - shouldn't happen!
            if __debug__:
                print self.replaylog[0]
                print_exc()
            self.replaylog.pop(0)	# Discard and continue
        else:
            # Rewrite old schema name
            if msg['$schemaRef'].startswith('http://schemas.elite-markets.net/eddn/'):
                msg['$schemaRef'] = 'https://ceddn.canonn.tech/schemas/' + msg['$schemaRef'][38:]
            try:
                self.send(cmdr, msg)
                self.replaylog.pop(0)
                if not len(self.replaylog) % self.REPLAYFLUSH:
                    self.flush()
            except requests.exceptions.RequestException as e:
                if __debug__: print_exc()
                status['text'] = _("Error: Can't connect to CEDDN")
                return	# stop sending
            except Exception as e:
                if __debug__: print_exc()
                status['text'] = unicode(e)
                return	# stop sending

        self.parent.after(self.REPLAYPERIOD, self.sendreplay)

    def export_journal_entry(self, cmdr, is_beta, entry):
        msg = {
            '$schemaRef' : 'https://ceddn.canonn.tech/schemas/journal/1' + (is_beta and '/test' or ''),
            'message'    : entry
        }
        if self.replayfile or self.load():
            # Store the entry
            self.replaylog.append(json.dumps([cmdr.encode('utf-8'), msg]))
            self.replayfile.write('%s\n' % self.replaylog[-1])

            if (entry['event'] == 'Docked' or
                (entry['event'] == 'Location' and entry['Docked']) or
                not (config.getint('output') & config.OUT_SYS_DELAY)):
                self.parent.after(self.REPLAYPERIOD, self.sendreplay)	# Try to send this and previous entries
        else:
            # Can't access replay file! Send immediately.
            status = self.parent.children['status']
            status['text'] = _('Sending data to CEDDN...')
            self.parent.update_idletasks()
            self.send(cmdr, msg)
            status['text'] = ''

    def canonicalise(self, item):
        match = self.CANONICALISE_RE.match(item)
        return match and match.group(1) or item


# Plugin callbacks

def plugin_start():
    return 'CEDDN'

def plugin_app(parent):
    this.parent = parent
    this.ceddn = CEDDN(parent)
    # Try to obtain exclusive lock on journal cache, even if we don't need it yet
    if not this.ceddn.load():
        this.status['text'] = 'Error: Is another copy of this app already running?'	# Shouldn't happen - don't bother localizing

def plugin_prefs(parent, cmdr, is_beta):

    PADX = 10
    BUTTONX = 12	# indent Checkbuttons and Radiobuttons
    PADY = 2		# close spacing

    output = config.getint('output') or (config.OUT_CDX_CEDDN | config.OUT_MAT_CEDDN)	# default settings

    ceddnframe = nb.Frame(parent)

    HyperlinkLabel(ceddnframe, text='Canonn ED Data Network', background=nb.Label().cget('background'), url='https://github.com/canonn-science/ceddn', underline=True).grid(padx=PADX, sticky=tk.W)	# Don't translate
    this.ceddn_codex= tk.IntVar(value = (output & config.OUT_CDX_CEDDN) and 1)
    this.ceddn_codex_button = nb.Checkbutton(ceddnframe, text=_('Send codex data to the Canonn ED Data Network'), variable=this.ceddn_codex, command=prefsvarchanged)	# Output setting
    this.ceddn_codex_button.grid(padx=BUTTONX, pady=(5,0), sticky=tk.W)
    this.ceddn_material = tk.IntVar(value = (output & config.OUT_MAT_CEDDN) and 1)
    this.ceddn_material_button = nb.Checkbutton(ceddnframe, text=_('Send material data to the Canonn ED Data Network'), variable=this.ceddn_material, command=prefsvarchanged)	# Output setting new in E:D 2.2
    this.ceddn_material_button.grid(padx=BUTTONX, pady=(5,0), sticky=tk.W)


    return ceddnframe

def prefsvarchanged(event=None):
    this.ceddn_codex_button['state'] = tk.NORMAL
    this.ceddn_material_button['state']= tk.NORMAL

def prefs_changed(cmdr, is_beta):
    config.set('output',
               #(config.getint('output') & (config.OUT_MKT_TD | config.OUT_MKT_CSV | config.OUT_SHIP |config. OUT_MKT_MANUAL)) +
               (this.ceddn_codex.get() and config.OUT_CDX_CEDDN) +
               (this.ceddn_material.get() and config.OUT_MAT_CEDDN))

def plugin_stop():
    this.ceddn.close()

def codex_entry(cmdr, is_beta, system, station, entry, state):

    # Recursively filter '*_Localised' keys from dict
    def filter_localised(d):
        filtered = OrderedDict()
        for k, v in d.iteritems():
            if k.endswith('_Localised'):
                pass
            elif hasattr(v, 'iteritems'):	# dict -> recurse
                filtered[k] = filter_localised(v)
            elif isinstance(v, list):	# list of dicts -> recurse
                filtered[k] = [filter_localised(x) if hasattr(x, 'iteritems') else x for x in v]
            else:
                filtered[k] = v
        return filtered

    # Track location
    if entry['event'] in ['CodexEntry']:
        if entry['event'] == 'Location':
            this.planet = entry.get('Body') if entry.get('BodyType') == 'Planet' else None
        elif entry['event'] == 'FSDJump':
            this.planet = None
        if 'StarPos' in entry:
            this.coordinates = tuple(entry['StarPos'])
        elif this.systemaddress != entry.get('SystemAddress'):
            this.coordinates = None	# Docked event doesn't include coordinates
        this.systemaddress = entry.get('SystemAddress')
    elif entry['event'] == 'ApproachBody':
        this.planet = entry['Body']
    elif entry['event'] in ['LeaveBody', 'SupercruiseEntry']:
        this.planet = None

    # Send interesting events to CEDDN, but not when on a crew
    if (config.getint('output') & config.OUT_SYS_CEDDN and not state['Captain'] and
        (entry['event'] == 'Location' or
         entry['event'] == 'FSDJump' or
         entry['event'] == 'Docked'  or
         entry['event'] == 'Scan'    and this.coordinates)):
        # strip out properties disallowed by the schema
        for thing in ['ActiveFine', 'CockpitBreach', 'BoostUsed', 'FuelLevel', 'FuelUsed', 'JumpDist', 'Latitude', 'Longitude', 'Wanted']:
            entry.pop(thing, None)
        if 'Factions' in entry:
            # Filter faction state. `entry` is a shallow copy so replace 'Factions' value rather than modify in-place.
            entry['Factions'] = [ {k: v for k, v in f.iteritems() if k not in ['HappiestSystem', 'HomeSystem', 'MyReputation', 'SquadronFaction']} for f in entry['Factions']]

        # add planet to Docked event for planetary stations if known
        if entry['event'] == 'Docked' and this.planet:
            entry['Body'] = this.planet
            entry['BodyType'] = 'Planet'

        # add mandatory StarSystem, StarPos and SystemAddress properties to Scan events
        if 'StarSystem' not in entry:
            entry['StarSystem'] = system
        if 'StarPos' not in entry:
            entry['StarPos'] = list(this.coordinates)
        if 'SystemAddress' not in entry and this.systemaddress:
            entry['SystemAddress'] = this.systemaddress

        try:
            this.ceddn.export_journal_entry(cmdr, is_beta, filter_localised(entry))

        except requests.exceptions.RequestException as e:
            if __debug__: print_exc()
            return _("Error: Can't connect to CEDDN")
        except Exception as e:
            if __debug__: print_exc()
            return unicode(e)
