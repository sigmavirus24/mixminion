# Copyright 2002 Nick Mathewson.  See LICENSE for licensing information.
# $Id: ServerInfo.py,v 1.8 2002/08/06 16:09:21 nickm Exp $

"""mixminion.ServerInfo

   Implementation of server descriptors (as described in the mixminion
   spec).  Includes logic to parse, validate, and generate server
   descriptors.
   """

__all__ = [ 'ServerInfo' ]

import time
import os
import binascii
import socket

from mixminion.Modules import SWAP_FWD_TYPE, FWD_TYPE
from mixminion.Packet import IPV4Info
import mixminion.Config
import mixminion.Crypto

ConfigError = mixminion.Config.ConfigError

# Longest allowed Nickname
MAX_NICKNAME = 128
# Longest allowed Contact email
MAX_CONTACT = 256
# Longest allowed Comments field
MAX_COMMENTS = 1024
# Shortest permissible identity key
MIN_IDENTITY_BYTES = 2048 >> 3
# Longest permissible identity key
MAX_IDENTITY_BYTES = 4096 >> 3
# Length of packet key
PACKET_KEY_BYTES = 1024 >> 3

# tmp alias to make this easier to spell.
C = mixminion.Config
class ServerInfo(mixminion.Config._ConfigFile):
    """A ServerInfo object holds a parsed server descriptor."""
    _restrictFormat = 1
    _syntax = {
	"Server" : { "__SECTION__": ("REQUIRE", None, None),
                     "Descriptor-Version": ("REQUIRE", None, None),
		     "IP": ("REQUIRE", C._parseIP, None),
		     "Nickname": ("REQUIRE", None, None),
		     "Identity": ("REQUIRE", C._parsePublicKey, None),
		     "Digest": ("REQUIRE", C._parseBase64, None),
		     "Signature": ("REQUIRE", C._parseBase64, None),
		     "Published": ("REQUIRE", C._parseTime, None),
		     "Valid-After": ("REQUIRE", C._parseDate, None),
		     "Valid-Until": ("REQUIRE", C._parseDate, None),
		     "Contact": ("ALLOW", None, None),
		     "Comments": ("ALLOW", None, None),
		     "Packet-Key": ("REQUIRE", C._parsePublicKey, None),
		     },
	"Incoming/MMTP" : {
 	             "Version": ("REQUIRE", None, None),
		     "Port": ("REQUIRE", C._parseInt, None),
		     "Key-Digest": ("REQUIRE", C._parseBase64, None),
		     "Protocols": ("REQUIRE", None, None),
                     "Allow": ("ALLOW*", C._parseAddressSet_allow, None),
                     "Deny": ("ALLOW*", C._parseAddressSet_deny, None),
		     },
	"Outgoing/MMTP" : {
 	             "Version": ("REQUIRE", None, None),
		     "Protocols": ("REQUIRE", None, None),
                     "Allow": ("ALLOW*", C._parseAddressSet_allow, None),
                     "Deny": ("ALLOW*", C._parseAddressSet_deny, None),
		     },
	"Delivery/MBOX" : {
   	             "Version": ("REQUIRE", None, None),
		     },
	"Delivery/SMTP" : {
           	     "Version": ("REQUIRE", None, None),
		     }
	}

    def __init__(self, fname=None, string=None, assumeValid=0):
	mixminion.Config._ConfigFile.__init__(self, fname, string, assumeValid)

    def validate(self, sections, entries, lines, contents):
	####
	# Check 'Server' section.
	server = sections['Server']
	if server['Descriptor-Version'] != '1.0':
	    raise ConfigError("Unrecognized descriptor version")
	if len(server['Nickname']) > MAX_NICKNAME:
	    raise ConfigError("Nickname too long")
	identityKey = server['Identity']
	identityBytes = identityKey.get_modulus_bytes()
	if not (MIN_IDENTITY_BYTES <= identityBytes <= MAX_IDENTITY_BYTES):
	    raise ConfigError("Invalid length on identity key")
	if server['Valid-Until'] <= server['Valid-After']:
	    raise ConfigError("Server is never valid")
	if server['Contact'] and len(server['Contact']) > MAX_CONTACT:
	    raise ConfigError("Contact too long")
        if server['Comments'] and len(server['Comments']) > MAX_COMMENTS:
	    raise ConfigError("Comments too long")
	packetKeyBytes = server['Packet-Key'].get_modulus_bytes()
	if packetKeyBytes != PACKET_KEY_BYTES:
	    raise ConfigError("Invalid length on packet key")

	####
	# Check Digest of file
	digest = getServerInfoDigest(contents)
	if digest != server['Digest']:
	    raise ConfigError("Invalid digest")
	
	if digest != mixminion.Crypto.pk_check_signature(server['Signature'],
							 identityKey):
	    raise ConfigError("Invalid signature")

	#### XXXX CHECK OTHER SECTIONS

    def getAddr(self):
	return self['Server']['IP']

    def getPort(self):
	return self['Incoming/MMTP']['Port']

    def getPacketKey(self):
	return self['Server']['Packet-Key']

    def getKeyID(self):
	return self['Incoming/MMTP']['Key-Digest']

    def getRoutingInfo(self):
        """Returns a mixminion.Packet.IPV4Info object for routing messages
           to this server."""
        return IPV4Info(self.getAddr(), self.getPort(), self.getKeyID())

#----------------------------------------------------------------------
# This should go in a different file.
class ServerKeys:
    """A set of expirable keys for use by a server.

       A server has one long-lived identity key, and two short-lived
       temporary keys: one for subheader encryption and one for MMTP.  The
       subheader (or 'packet') key has an associated hashlog, and the
       MMTP key has an associated self-signed certificate.

       Whether we publish or not, we always generate a server descriptor
       to store the keys' lifetimes.

       When we create a new ServerKeys object, the associated keys are not
       read from disk unil the object's load method is called."""
    def __init__(self, keyroot, keyname, hashroot):
	keydir = self.keydir = os.path.join(keyroot, "key_"+keyname)
	self.hashlogFile = os.path.join(hashroot, "hash_"+keyname)
	self.packetKeyFile = os.path.join(keydir, "mix.key")
	self.mmtpKeyFile = os.path.join(keydir, "mmtp.key")
	self.certFile = os.path.join(keydir, "mmtp.cert")
        self.descFile = os.path.join(keydir, "ServerDesc")
        if not os.path.exists(keydir):
            os.mkdir(keydir, 0700)
        
    def load(self, password=None):
        "Read this set of keys from disk."
        self.packetKey = mixminion.Crypto.pk_PEM_load(self.packetKeyFile,
                                                      password)
        self.mmtpKey = mixminion.Crypto.pk_PEM_load(self.mmtpKeyFile,
                                                    password)
    def save(self, password=None):
        "Save this set of keys to disk."
        mixminion.Crypto.pk_PEM_save(self.packetKey, self.packetKeyFile,
                                     password)
        mixminion.Crypto.pk_PEM_save(self.mmtpKey, self.mmtpKeyFile,
                                     password)
    def getCertFileName(self): return self.certFile
    def getHashLogFileName(self): return self.hashlogFile
    def getDescriptorFileName(self): return self.descFile
    def getPacketKey(self): return self.packetKey
    def getMMTPKey(self): return self.mmtpKey
    def getMMTPKeyID(self):
        "Return the sha1 hash of the asn1 encoding of the MMTP public key"
	return mixminion.Crypto.sha1(self.mmtpKey.encode_key(1))

def _base64(s):
    "Helper function: returns a one-line base64 encoding of a given string."
    return binascii.b2a_base64(s).replace("\n","")

def _time(t):
    """Helper function: turns a time (in seconds) into the format used by
       Server descriptors"""
    gmt = time.gmtime(t)
    return "%02d/%02d/%04d %02d:%02d:%02d" % (
	gmt[2],gmt[1],gmt[0],  gmt[3],gmt[4],gmt[5])

def _date(t):
    """Helper function: turns a time (in seconds) into a date in the format
       used by server descriptors"""
    gmt = time.gmtime(t+1) # Add 1 to make sure we round down.
    return "%02d/%02d/%04d" % (gmt[2],gmt[1],gmt[0])

def _rule(allow, (ip, mask, portmin, portmax)):
    if mask == '0.0.0.0':
        ip="*"
        mask=""
    elif mask == "255.255.255.255":
        mask = ""
    else:
        mask = "/%s" % mask

    if portmin==portmax==48099 and allow:
        ports = ""
    elif portmin == 0 and portmax == 65535 and not allow:
        ports = ""
    elif portmin == portmax:
        ports = " %s" % portmin
    else:
        ports = " %s-%s" % (portmin, portmax)

    return "%s%s%s\n" % (ip,mask,ports)

# We have our X509 certificate set to expire a bit after public key does,
# so that slightly-skewed clients don't incorrectly give up while trying to
# connect to us.
CERTIFICATE_EXPIRY_SLOPPINESS = 5*60

def generateServerDescriptorAndKeys(config, identityKey, keydir, keyname,
                                    hashdir,
                                    validAt=None):
    """Generate and sign a new server descriptor, and generate all the keys to
       go with it.

          identityKey -- This server's private identity key
          keydir -- The root directory for storing key sets.
          keyname -- The name of this new key set within keydir
          validAt -- The starting time (in seconds) for this key's lifetime."""

    packetKey = mixminion.Crypto.pk_generate(PACKET_KEY_BYTES*8)
    mmtpKey = mixminion.Crypto.pk_generate(PACKET_KEY_BYTES*8)

    serverKeys = ServerKeys(keydir, keyname, hashdir)
    serverKeys.packetKey = packetKey
    serverKeys.mmtpKey = mmtpKey
    serverKeys.save()

    allowIncoming = config['Incoming/MMTP'].get('Enabled', 0)

    nickname = config['Server']['Nickname']
    if not nickname:
        nickname = socket.gethostname()
        if not nickname or nickname.lower().startswith("localhost"):
            nickname = config['Incoming/MMTP'].get('IP', "<Unknown host>")
    contact = config['Server']['Contact-Email']
    comments = config['Server']['Comments']
    if not validAt:
        validAt = time.time()

    validUntil = validAt + config['Server']['PublicKeyLifetime'][2]
    certStarts = validAt - CERTIFICATE_EXPIRY_SLOPPINESS
    certEnds = validUntil + CERTIFICATE_EXPIRY_SLOPPINESS + \
               config['Server']['PublicKeySloppiness'][2]

    mixminion.Crypto.generate_cert(serverKeys.getCertFileName(),
				   mmtpKey,
				   "MMTP certificate for %s" %nickname,
                                   certStarts, certEnds)

    fields = {
	"IP": config['Incoming/MMTP'].get('IP', "0.0.0.0"),
	"Port": config['Incoming/MMTP'].get('Port', 0),
	"Nickname": nickname,
	"Identity":
	   _base64(mixminion.Crypto.pk_encode_public_key(identityKey)),
	"Published": _time(time.time()),
	"ValidAfter": _date(validAt),
	"ValidUntil": _date(validUntil),
	"PacketKey":
  	   _base64(mixminion.Crypto.pk_encode_public_key(packetKey)),
	"KeyID":
	   _base64(serverKeys.getMMTPKeyID()),
	}
	
    info = """\
        [Server]
	Descriptor-Version: 1.0
        IP: %(IP)s
        Nickname: %(Nickname)s
	Identity: %(Identity)s
	Digest:
        Signature:
        Published: %(Published)s
        Valid-After: %(ValidAfter)s
	Valid-Until: %(ValidUntil)s
	Packet-Key: %(PacketKey)s
        """ % fields
    if contact:
	info += "Contact: %s\n"%contact
    if comments:
	info += "Comments: %s\n"%comments

    if config["Incoming/MMTP"].get("Enabled", 0):
	info += """\
            [Incoming/MMTP]
            Version: 1.0
            Port: %(Port)s
	    Key-Digest: %(KeyID)s
	    Protocols: 1.0
            """ % fields
        for k,v in config.getSectionItems("Incoming/MMTP"):
            if k not in ("Allow", "Deny"):
                continue
            info += "%s: %s" % (k, _rule(k=='Allow',v))

    if config["Outgoing/MMTP"].get("Enabled", 0):
	info += """\
            [Outgoing/MMTP]
	    Version: 1.0
            Protocols: 1.0
            """
        for k,v in config.getSectionItems("Outgoing/MMTP"):
            if k not in ("Allow", "Deny"):
                continue
            info += "%s: %s" % (k, _rule(k=='Allow',v))

    info += "".join(config.moduleManager.getServerInfoBlocks())

    # Remove extra (leading) whitespace.
    lines = [ line.strip() for line in info.split("\n") ]
    # Remove empty lines
    lines = filter(None, lines)
    # Force a newline at the end of the file, rejoin, and sign.
    lines.append("")
    info = "\n".join(lines)
    info = signServerInfo(info, identityKey)

    f = open(serverKeys.getDescriptorFileName(), 'w')
    try:
        f.write(info)
    finally:
        f.close()

    # for debugging XXXX ### Remove this once we're more confident.
    ServerInfo(string=info)

    return info

#-----------------------b-----------------------------------------------
def getServerInfoDigest(info):
    """Calculate the digest of a server descriptor"""
    return _getServerInfoDigestImpl(info, None)

def signServerInfo(info, rsa):
    """Sign a server descriptor.  <info> should be a well-formed server
       descriptor, with Digest: and Signature: lines present but with
       no values."""       
    return _getServerInfoDigestImpl(info, rsa)

def _getServerInfoDigestImpl(info, rsa=None):
    """Helper method.  Calculates the correct digest of a server descriptor
       (as provided in a string).  If rsa is provided, signs the digest and
       creates a new descriptor.  Otherwise just returns the digest."""

    # The algorithm's pretty easy.  We just find the Digest and Signature
    # lines, replace each with an 'Empty' version, and calculate the digest.
    infoLines = info.split("\n")
    if not infoLines[0] == "[Server]":
        raise ConfigError("Must begin with server section")
    digestLine = None
    signatureLine = None
    infoLines = info.split("\n")
    for lineNo in range(len(infoLines)):
	line = infoLines[lineNo]
	if line.startswith("Digest:") and digestLine is None:
	    digestLine = lineNo
	elif line.startswith("Signature:") and signatureLine is None:
	    signatureLine = lineNo

    assert digestLine is not None and signatureLine is not None

    infoLines[digestLine] = 'Digest:'
    infoLines[signatureLine] = 'Signature:'
    info = "\n".join(infoLines)

    digest = mixminion.Crypto.sha1(info)

    if rsa is None:
	return digest
    # If we got an RSA key, we need to add the digest and signature.

    signature = mixminion.Crypto.pk_sign(digest,rsa)
    digest = _base64(digest)
    signature = binascii.b2a_base64(signature).replace("\n","")
    infoLines[digestLine] = 'Digest: '+digest
    infoLines[signatureLine] = 'Signature: '+signature

    return "\n".join(infoLines)
