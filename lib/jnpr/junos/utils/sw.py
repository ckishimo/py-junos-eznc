# stdlib
import hashlib
from os import path

# 3rd-party modules
from lxml.builder import E

# local modules
from .util import Util
from .scp import SCP 

__all__ = ['SW']

def _hashfile(afile, hasher,blocksize=65536):
  buf = afile.read(blocksize)
  while len(buf) > 0:
      hasher.update(buf)
      buf = afile.read(blocksize)
  return hasher.hexdigest()      

class SW(Util):
  """
  Softawre Utility class, used to perform a software upgrade and associated functions.

  Primary methods:
    install - perform the entire software installation process
    reboot - reboots the system for the new image to take effect    
    poweroff - shutdown the system

  Helpers called from install, but you can use these individually if needed:
    put - SCP put package file onto Junos device
    pkgadd - performs the 'request' operation to install the package
    validate - performs the 'request' to validate the package

  Other utils:
    rollback - same as 'request softare rollback'
    inventory - (property) provides file info for current and rollback 
                images on the device
  """

  ##### -----------------------------------------------------------------------
  ##### CLASS METHODS
  ##### -----------------------------------------------------------------------

  @classmethod
  def local_sha256(cls,package):
    """
    computes the SHA-256 value on the package file.

    :package:
      complete path to the package (*.tgz) file on the local server      
    """
    return _hashfile(open(package,'rb'),hashlib.sha256())

  @classmethod
  def local_md5(cls,package):
    """
    computes the MD5 checksum value on the local package file.  

    :package:
      complete path to the package (*.tgz) file on the local server      
    """
    return _hashfile(open(package,'rb'),hashlib.md5())

  @classmethod
  def local_sha1(cls,package):
    """
    computes the SHA1 checksum value on the local package file.  

    :package:
      complete path to the package (*.tgz) file on the local server      
    """
    return _hashfile(open(package,'rb'),hashlib.sha1())    

  @classmethod
  def progress(cls,dev,report):
    """ simple progress report function """
    print dev.hostname + ": " + report

  ### -------------------------------------------------------------------------
  ### put - SCP put the image onto the device
  ### -------------------------------------------------------------------------

  def put(self, package, remote_path='/var/tmp', progress=None):
    """
    SCP 'put' the package file from the local server to the remote device.

    :package:
      file path to the package file on the local file system

    :remote_path:
      the directory on the device where the package will be copied to

    :progress:
      callback function to indicate progress.  You can use :SW.progress:
      for basic reporting.  See that class method for details.
    """
    def _progress(report): 
      # report progress only if a progress callback was provided
      if progress is not None: progress(self._dev, report)

    def _scp_progress(_path, _total, _xfrd):
      # init static variable
      if not hasattr(_scp_progress,'by10pct'): _scp_progress.by10pct = 0

      # calculate current percentage xferd
      pct = int(float(_xfrd)/float(_total) * 100)

      # if 10% more has been copied, then print a message
      if 0 == (pct % 10) and pct != _scp_progress.by10pct:
        _scp_progress.by10pct = pct
        _progress("%s: %s / %s (%s%%)" % (_path,_xfrd,_total,str(pct)))

    # execute the secure-copy with the Python SCP module

    with SCP(self._dev, progress=_scp_progress) as scp:
      scp.put(package, remote_path)

  ### -------------------------------------------------------------------------
  ### pkgadd - used to perform the 'request system software add ...'
  ### -------------------------------------------------------------------------

  def pkgadd(self, remote_package, **kvargs ):    
    """ 
    Issue the 'request system software add' command on the package.  No 
    validate is auto-set.  If you want to validate the image, do that 
    using the specific :validate(): method.  Also, if you want to
    reboot the device, suggest using the :reboot(): method rather
    than kvargs['reboot']=True.

    :remote_package:
      the file-path to the install package

    :kvargs:
      any additional parameters to the 'request' command can
      be passed within kvargs, following the RPC syntax
      methodology (dash-2-underscore,etc.)
    """

    args = dict(no_validate=True, package_name=remote_package)
    args.update(kvargs)

    dev_to = self.dev.timeout     # store device/rpc timeout
    self.dev.timeout = 60*60      # hardset to 1 hr for long running process
    rsp = self.rpc.request_package_add(**args)
    self.dev.timeout = dev_to     # restore original timeout

    got = rsp.getparent()
    rc = int(got.findtext('package-result').strip())
    return True if rc == 0 else got.findtext('output').strip()

  ### -------------------------------------------------------------------------
  ### validate - perform 'request' operation to validate the package
  ### -------------------------------------------------------------------------

  def validate(self, remote_package):
    """ issues the 'request' operation to validate the package against the config """
    rsp = self.rpc.request_package_validate(package_name=remote_package).getparent()
    errcode = int(rsp.findtext('package-result'))
    return True if 0 == errcode else rsp.findtext('output').strip()

  def remote_checksum(self, remote_package):
    """ computes the MD5 checksum on the remote device """
    rsp = self.rpc.get_checksum_information(path=remote_package)
    return rsp.findtext('.//checksum').strip()    

  ### -------------------------------------------------------------------------
  ### safe_copy - copies the package and performs checksum
  ### -------------------------------------------------------------------------

  def safe_copy(self, package, **kvargs):
    """
    Copy the install package safely to the remote device.  By default
    this means to clean the filesystem to make space, perform the 
    secure-copy, and then verify the MD5 checksum.

    For :kvargs: values, please refer to the :install(): method.
    """
    remote_path = kvargs.get('remote_path','/var/tmp')
    progress = kvargs.get('progress')
    checksum = kvargs.get('checksum')
    cleanfs = kvargs.get('cleanfs', True)

    def _progress(report): 
      if progress is not None: progress(self._dev, report)

    if checksum is None: 
      _progress('computing local checksum on: %s' % package)
      checksum = SW.local_md5(package)

    if cleanfs is True:
      _progress('cleaning filesystem ...')
      self.rpc.request_system_storage_cleanup()

    # we want to give the caller an override so we don't always
    # need to copy the file, but the default is to do this, yo!
    self.put( package, remote_path, progress)

    # validate checksum:
    remote_package = remote_path + '/' + path.basename(package)
    _progress('computing remote checksum on: %s' % remote_package)
    remote_checksum = self.remote_checksum(remote_package)

    if remote_checksum != checksum:
      _progress("checksum check failed.")
      return False
    _progress("checksum check passed.")    

    return True

  ### -------------------------------------------------------------------------
  ### install - complete installation process, but not reboot
  ### -------------------------------------------------------------------------

  def install(self, package, remote_path='/var/tmp', progress=None,
    validate=False, checksum=None, cleanfs=True, no_copy=False):
    """
    Performs the complete installation of the :package: that includes the following steps:
      (1) computes the MD5 checksum if not provided in :checksum:
      (2) performs a storage cleanup if :cleanfs: is True
      (3) SCP copies the package to the :remote_path: directory
      (4) validates the package if :validate: is True
      (5) installs the package

    You can get a progress report on this process by providing a :progress: callback;
    see description below.

    You will need to invoke the :reboot(): method explicity to reboot the device.

    :package: 
      is the install package tarball on the local filesystem.
    
    :remote_path: 
      is the directory on the Junos device where the package file will be SCP'd to.  
    
    :validate:
      determines whether or not to perform a config validation against the new image

    :checksum:
      MD5 hexdigest of the package file.  If this is not provided, then this
      method will perform the calculation.  If you are planning on using the
      same image for multiple updates, you should consider using the :local_md5():
      method to precalculate this value and then provide to this method.

    :cleanfs:
      determines whether or not to perform a 'storeage cleanup' before SCP'ing 
      the file to the device.

    :progress:
      if provided, this is a callback function with a function prototype given
      the Device instance and the report string, e.g.
        
        def myprogress(dev, report):
          print "host: %s, report: %s" % (dev.hostname, report)
    """
    def _progress(report): 
      if progress is not None: progress(self._dev, report)
    
    rpc = self.rpc

    ### -----------------------------------------------------------------------
    ### perform a 'safe-copy' of the image to the remote device
    ### -----------------------------------------------------------------------

    if no_copy is False:
      copy_ok = self.safe_copy(package, remote_path=remote_path, progress=progress,
        cleanfs=cleanfs, checksum=checksum)
      if copy_ok is False: return False

    ### -----------------------------------------------------------------------
    ### at this point, the file exists on the remote device
    ### -----------------------------------------------------------------------

    remote_package = remote_path + '/' + path.basename(package)

    if validate is True:
      _progress("validating software against current config, please be patient ...")
      v_ok = self.validate(remote_package)
      if v_ok is not True:
        return v_ok # will be the string of output

    _progress("installing software ... this could take some time, please be patient ...")
    rsp = self.pkgadd( remote_package )
    return rsp

  ### -------------------------------------------------------------------------
  ### rebbot - system reboot
  ### -------------------------------------------------------------------------

  def reboot(self, in_min=0):    
    """ 
    Perform a system reboot, with optional delay (in minutes).  

    If the device is an MX with dual-RE installed, then both RE will be
    rebooted.
    """
    cmd = E('request-reboot', E('in', str(in_min)))

    _facts = self.dev.facts
    if _facts['personality'] == 'MX' and (_facts.has_key('RE0') and _facts.has_key('RE1')):
      cmd.append(E('both-routing-engines'))

    rsp = self.rpc(cmd)
    got = rsp.getparent().findtext('.//request-reboot-status').strip()
    return got

  ### -------------------------------------------------------------------------
  ### poweroff - system shutdown
  ### -------------------------------------------------------------------------

  def poweroff(self, in_min=0):    
    """
    Perform a system shutdown, with optional delay (in minutes) .

    If the device is an MX with dual-RE installed, then both RE will be
    rebooted.    
    """
    cmd = E('request-power-off', E('in', str(in_min)))

    _facts = self.dev.facts
    if _facts['personality'] == 'MX' and (_facts.has_key('RE0') and _facts.has_key('RE1')):
      cmd.append(E('both-routing-engines'))

    rsp = self.rpc(cmd)
    got = rsp.getparent().findtext('.//request-reboot-status').strip()
    return got

  ### -------------------------------------------------------------------------
  ### rollback - clears the install request
  ### -------------------------------------------------------------------------

  def rollback(self):
    """ 
    issues the 'request' command to do the rollback and returns the string
    output of the results
    """
    rsp = self.rpc.request_package_rollback()
    return rsp.text.strip()

  ### -------------------------------------------------------------------------
  ### inventory - file info on current and rollback packages
  ### -------------------------------------------------------------------------

  @property
  def inventory(self):
    """
    returns a dictionary of file listing information for current and rollback
    Junos install packages.  This information comes from the /packages directory.
    """
    from .fs import FS 
    fs = FS(self.dev)
    pkgs = fs.ls('/packages') 
    return dict(current=pkgs['files'].get('junos'), rollback=pkgs['files'].get('junos.old'))
