#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2023 Battelle Energy Alliance, LLC.  All rights reserved.

import sys

sys.dont_write_bytecode = True

import argparse
import datetime
import errno
import fileinput
import getpass
import glob
import json
import os
import pathlib
import platform
import pprint
import math
import re
import shutil
import tarfile
import tempfile
import time

try:
    from pwd import getpwuid
except ImportError:
    getpwuid = None
from collections import defaultdict, namedtuple

from malcolm_common import (
    AskForString,
    ChooseMultiple,
    ChooseOne,
    DetermineYamlFileFormat,
    DisplayMessage,
    DOCKER_COMPOSE_INSTALL_URLS,
    DOCKER_INSTALL_URLS,
    DotEnvDynamic,
    DownloadToFile,
    HOMEBREW_INSTALL_URLS,
    KubernetesDynamic,
    MalcolmCfgRunOnceFile,
    MalcolmPath,
    OrchestrationFramework,
    OrchestrationFrameworksSupported,
    PLATFORM_LINUX,
    PLATFORM_LINUX_CENTOS,
    PLATFORM_LINUX_DEBIAN,
    PLATFORM_LINUX_FEDORA,
    PLATFORM_LINUX_UBUNTU,
    PLATFORM_MAC,
    PLATFORM_WINDOWS,
    PROFILE_MALCOLM,
    PROFILE_HEDGEHOG,
    PROFILE_KEY,
    ReplaceBindMountLocation,
    RequestsDynamic,
    ScriptPath,
    UserInputDefaultsBehavior,
    UserInterfaceMode,
    YAMLDynamic,
    YesOrNo,
)
from malcolm_utils import (
    CountUntilException,
    DatabaseMode,
    DATABASE_MODE_LABELS,
    DATABASE_MODE_ENUMS,
    deep_get,
    eprint,
    run_process,
    same_file_or_dir,
    str2bool,
    touch,
    which,
)

###################################################################################################
DOCKER_COMPOSE_INSTALL_VERSION = "2.20.3"

DEB_GPG_KEY_FINGERPRINT = '0EBFCD88'  # used to verify GPG key for Docker Debian repository

MAC_BREW_DOCKER_PACKAGE = 'docker-edge'
MAC_BREW_DOCKER_SETTINGS = '/Users/{}/Library/Group Containers/group.com.docker/settings.json'

LOGSTASH_JAVA_OPTS_DEFAULT = '-server -Xms2500m -Xmx2500m -Xss1536k -XX:-HeapDumpOnOutOfMemoryError -Djava.security.egd=file:/dev/./urandom -Dlog4j.formatMsgNoLookups=true'
OPENSEARCH_JAVA_OPTS_DEFAULT = '-server -Xms10g -Xmx10g -Xss256k -XX:-HeapDumpOnOutOfMemoryError -Djava.security.egd=file:/dev/./urandom -Dlog4j.formatMsgNoLookups=true'

###################################################################################################
ScriptName = os.path.basename(__file__)
origPath = os.getcwd()

###################################################################################################
args = None
requests_imported = None
yaml_imported = None
kube_imported = None
dotenv_imported = None

###################################################################################################
TrueOrFalseQuote = lambda x: "'true'" if x else "'false'"
TrueOrFalseNoQuote = lambda x: 'true' if x else 'false'
MaxAskForValueCount = 100


###################################################################################################
# get interactive user response to Y/N question
def InstallerYesOrNo(
    question,
    default=None,
    forceInteraction=False,
    defaultBehavior=UserInputDefaultsBehavior.DefaultsPrompt | UserInputDefaultsBehavior.DefaultsAccept,
    uiMode=UserInterfaceMode.InteractionInput | UserInterfaceMode.InteractionDialog,
    yesLabel='Yes',
    noLabel='No',
):
    global args
    defBehavior = defaultBehavior
    if args.acceptDefaultsNonInteractive and not forceInteraction:
        defBehavior = defBehavior + UserInputDefaultsBehavior.DefaultsNonInteractive

    return YesOrNo(
        question,
        default=default,
        defaultBehavior=defBehavior,
        uiMode=uiMode,
        yesLabel=yesLabel,
        noLabel=noLabel,
    )


###################################################################################################
# get interactive user response string
def InstallerAskForString(
    question,
    default=None,
    forceInteraction=False,
    defaultBehavior=UserInputDefaultsBehavior.DefaultsPrompt | UserInputDefaultsBehavior.DefaultsAccept,
    uiMode=UserInterfaceMode.InteractionInput | UserInterfaceMode.InteractionDialog,
):
    global args
    defBehavior = defaultBehavior
    if args.acceptDefaultsNonInteractive and not forceInteraction:
        defBehavior = defBehavior + UserInputDefaultsBehavior.DefaultsNonInteractive

    return AskForString(
        question,
        default=default,
        defaultBehavior=defBehavior,
        uiMode=uiMode,
    )


###################################################################################################
# choose one from a list
def InstallerChooseOne(
    prompt,
    choices=[],
    forceInteraction=False,
    defaultBehavior=UserInputDefaultsBehavior.DefaultsPrompt | UserInputDefaultsBehavior.DefaultsAccept,
    uiMode=UserInterfaceMode.InteractionInput | UserInterfaceMode.InteractionDialog,
):
    global args
    defBehavior = defaultBehavior
    if args.acceptDefaultsNonInteractive and not forceInteraction:
        defBehavior = defBehavior + UserInputDefaultsBehavior.DefaultsNonInteractive

    return ChooseOne(
        prompt,
        choices=choices,
        defaultBehavior=defBehavior,
        uiMode=uiMode,
    )


###################################################################################################
# choose multiple from a list
def InstallerChooseMultiple(
    prompt,
    choices=[],
    forceInteraction=False,
    defaultBehavior=UserInputDefaultsBehavior.DefaultsPrompt | UserInputDefaultsBehavior.DefaultsAccept,
    uiMode=UserInterfaceMode.InteractionInput | UserInterfaceMode.InteractionDialog,
):
    global args
    defBehavior = defaultBehavior
    if args.acceptDefaultsNonInteractive and not forceInteraction:
        defBehavior = defBehavior + UserInputDefaultsBehavior.DefaultsNonInteractive

    return ChooseMultiple(
        prompt,
        choices=choices,
        defaultBehavior=defBehavior,
        uiMode=uiMode,
    )


###################################################################################################
# display a message to the user without feedback
def InstallerDisplayMessage(
    message,
    forceInteraction=False,
    defaultBehavior=UserInputDefaultsBehavior.DefaultsPrompt | UserInputDefaultsBehavior.DefaultsAccept,
    uiMode=UserInterfaceMode.InteractionInput | UserInterfaceMode.InteractionDialog,
):
    global args
    defBehavior = defaultBehavior
    if args.acceptDefaultsNonInteractive and not forceInteraction:
        defBehavior = defBehavior + UserInputDefaultsBehavior.DefaultsNonInteractive

    return DisplayMessage(
        message,
        defaultBehavior=defBehavior,
        uiMode=uiMode,
    )


###################################################################################################
class Installer(object):
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def __init__(self, orchMode, debug=False, configOnly=False):
        self.orchMode = orchMode
        self.debug = debug
        self.configOnly = configOnly

        self.platform = platform.system()
        self.scriptUser = getpass.getuser()

        self.checkPackageCmds = []
        self.installPackageCmds = []
        self.requiredPackages = []

        self.pipCmd = 'pip3'
        if not which(self.pipCmd, debug=self.debug):
            self.pipCmd = 'pip'

        self.tempDirName = tempfile.mkdtemp()

        self.totalMemoryGigs = 0.0
        self.totalCores = 0

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def __del__(self):
        shutil.rmtree(self.tempDirName, ignore_errors=True)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def run_process(self, command, stdout=True, stderr=True, stdin=None, privileged=False, retry=0, retrySleepSec=5):
        # if privileged, put the sudo command at the beginning of the command
        if privileged and (len(self.sudoCmd) > 0):
            command = self.sudoCmd + command

        return run_process(
            command,
            stdout=stdout,
            stderr=stderr,
            stdin=stdin,
            retry=retry,
            retrySleepSec=retrySleepSec,
            debug=self.debug,
        )

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def package_is_installed(self, package):
        result = False
        for cmd in self.checkPackageCmds:
            ecode, out = self.run_process(cmd + [package])
            if ecode == 0:
                result = True
                break
        return result

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def install_package(self, packages):
        result = False
        pkgs = []

        for package in packages:
            if not self.package_is_installed(package):
                pkgs.append(package)

        if len(pkgs) > 0:
            for cmd in self.installPackageCmds:
                ecode, out = self.run_process(cmd + pkgs, privileged=True)
                if ecode == 0:
                    result = True
                    break
        else:
            result = True

        return result

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def install_required_packages(self):
        if len(self.requiredPackages) > 0:
            eprint(f"Installing required packages: {self.requiredPackages}")
        return self.install_package(self.requiredPackages)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def install_docker_images(self, docker_image_file):
        result = False

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            if (
                docker_image_file
                and os.path.isfile(docker_image_file)
                and InstallerYesOrNo(
                    f'Load Malcolm Docker images from {docker_image_file}', default=True, forceInteraction=True
                )
            ):
                ecode, out = self.run_process(['docker', 'load', '-q', '-i', docker_image_file], privileged=True)
                if ecode == 0:
                    result = True
                else:
                    eprint(f"Loading Malcolm Docker images failed: {out}")

        return result

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def install_malcolm_files(self, malcolm_install_file, default_config_dir):
        global args

        result = False
        installPath = None
        if (
            malcolm_install_file
            and os.path.isfile(malcolm_install_file)
            and InstallerYesOrNo(
                f'Extract Malcolm runtime files from {malcolm_install_file}', default=True, forceInteraction=True
            )
        ):
            # determine and create destination path for installation
            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid installation path')
            while loopBreaker.increment():
                defaultPath = os.path.join(origPath, 'malcolm')
                installPath = InstallerAskForString(
                    f'Enter installation path for Malcolm [{defaultPath}]', default=defaultPath, forceInteraction=True
                )
                if len(installPath) == 0:
                    installPath = defaultPath
                if os.path.isdir(installPath):
                    eprint(f"{installPath} already exists, please specify a different installation path")
                else:
                    try:
                        os.makedirs(installPath)
                    except Exception:
                        pass
                    if os.path.isdir(installPath):
                        break
                    else:
                        eprint(f"Failed to create {installPath}, please specify a different installation path")

            # extract runtime files
            if installPath and os.path.isdir(installPath):
                MalcolmPath = installPath
                if self.debug:
                    eprint(f"Created {installPath} for Malcolm runtime files")

                tar = tarfile.open(malcolm_install_file)
                try:
                    tar.extractall(path=installPath, numeric_owner=True)
                finally:
                    tar.close()

                # .tar.gz normally will contain an intermediate subdirectory. if so, move files back one level
                childDir = glob.glob(f'{installPath}/*/')
                if (len(childDir) == 1) and os.path.isdir(childDir[0]):
                    if self.debug:
                        eprint(f"{installPath} only contains {childDir[0]}")
                    for f in os.listdir(childDir[0]):
                        shutil.move(os.path.join(childDir[0], f), installPath)
                    shutil.rmtree(childDir[0], ignore_errors=True)

                # create the config directory for the .env files
                if default_config_dir:
                    args.configDir = os.path.join(installPath, 'config')
                try:
                    os.makedirs(args.configDir)
                except OSError as exc:
                    if (exc.errno == errno.EEXIST) and os.path.isdir(args.configDir):
                        pass
                    else:
                        raise
                if self.debug:
                    eprint(f"Created {args.configDir} for Malcolm configuration files")

                # verify the installation worked
                if os.path.isfile(os.path.join(installPath, "docker-compose.yml")):
                    eprint(f"Malcolm runtime files extracted to {installPath}")
                    result = True
                    with open(os.path.join(installPath, "install_source.txt"), 'w') as f:
                        f.write(
                            f'{os.path.basename(malcolm_install_file)} (installed {str(datetime.datetime.now())})\n'
                        )
                else:
                    eprint(f"Malcolm install file extracted to {installPath}, but missing runtime files?")

        return result, installPath

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def tweak_malcolm_runtime(self, malcolm_install_path):
        global args
        global dotenv_imported

        configFiles = []

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            # determine docker-compose files
            if not args.configFile:
                # get a list of all of the docker-compose files
                configFiles = glob.glob(os.path.join(malcolm_install_path, 'docker-compose*.yml'))

            elif os.path.isfile(args.configFile):
                # single docker-compose file explicitly specified
                configFiles = [os.path.realpath(args.configFile)]
                malcolm_install_path = os.path.dirname(configFiles[0])

        elif self.orchMode is OrchestrationFramework.KUBERNETES:
            if args.configFile and os.path.isfile(args.configFile):
                configFiles = [os.path.realpath(args.configFile)]
                malcolm_install_path = os.path.realpath(os.path.join(ScriptPath, ".."))
            else:
                raise Exception(f"{self.orchMode} requires specifying kubeconfig file via -f/--config-file")

        if (not args.configDir) or (not os.path.isdir(args.configDir)):
            raise Exception("Could not determine configuration directory containing Malcolm's .env files")

        # figure out what UID/GID to run non-rood processes under docker as
        defaultUid = '1000'
        defaultGid = '1000'
        if ((self.platform == PLATFORM_LINUX) or (self.platform == PLATFORM_MAC)) and (self.scriptUser == "root"):
            defaultUid = str(os.stat(malcolm_install_path).st_uid)
            defaultGid = str(os.stat(malcolm_install_path).st_gid)

        puid = defaultUid
        pgid = defaultGid
        try:
            if self.platform == PLATFORM_LINUX:
                puid = str(os.getuid())
                pgid = str(os.getgid())
                if (puid == '0') or (pgid == '0'):
                    raise Exception('it is preferrable not to run Malcolm as root, prompting for UID/GID instead')
        except Exception:
            puid = defaultUid
            pgid = defaultGid

        loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid UID/GID')
        while (
            (not puid.isdigit())
            or (not pgid.isdigit())
            or (
                not InstallerYesOrNo(
                    f'Malcolm processes will run as UID {puid} and GID {pgid}. Is this OK?', default=True
                )
            )
        ) and loopBreaker.increment():
            puid = InstallerAskForString(
                'Enter user ID (UID) for running non-root Malcolm processes', default=defaultUid
            )
            pgid = InstallerAskForString(
                'Enter group ID (GID) for running non-root Malcolm processes', default=defaultGid
            )

        pcapNodeName = InstallerAskForString(
            f'Enter the node name to associate with network traffic metadata',
            default=args.pcapNodeName,
        )
        pcapNodeHost = ''

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            # guestimate how much memory we should use based on total system memory

            if self.debug:
                eprint(
                    f'{malcolm_install_path} with "{configFiles}" and "{args.configDir}", system memory is {self.totalMemoryGigs} GiB'
                )

            if self.totalMemoryGigs >= 63.0:
                osMemory = '24g'
                lsMemory = '3g'
            elif self.totalMemoryGigs >= 31.0:
                osMemory = '16g'
                lsMemory = '2500m'
            elif self.totalMemoryGigs >= 15.0:
                osMemory = '10g'
                lsMemory = '2500m'
            elif self.totalMemoryGigs >= 11.0:
                osMemory = '6g'
                lsMemory = '2g'
            elif self.totalMemoryGigs >= 7.0:
                eprint(f"Detected only {self.totalMemoryGigs} GiB of memory; performance will be suboptimal")
                osMemory = '4g'
                lsMemory = '2g'
            elif self.totalMemoryGigs > 0.0:
                eprint(f"Detected only {self.totalMemoryGigs} GiB of memory; performance will be suboptimal")
                osMemory = '3500m'
                lsMemory = '2g'
            else:
                eprint("Failed to determine system memory size, using defaults; performance may be suboptimal")
                osMemory = '8g'
                lsMemory = '3g'
        else:
            osMemory = '16g'
            lsMemory = '3g'

        # see Tuning and Profiling Logstash Performance
        # - https://www.elastic.co/guide/en/logstash/current/tuning-logstash.html
        # - https://www.elastic.co/guide/en/logstash/current/logstash-settings-file.html
        # - https://www.elastic.co/guide/en/logstash/current/multiple-pipelines.html
        # we don't want it too high, as in Malcolm Logstash also competes with OpenSearch, etc. for resources
        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            if self.totalCores > 16:
                lsWorkers = 6
            elif self.totalCores >= 12:
                lsWorkers = 4
            else:
                lsWorkers = 3
        else:
            lsWorkers = 6

        if args.osMemory:
            osMemory = args.osMemory
        if args.lsMemory:
            lsMemory = args.lsMemory
        if args.lsWorkers:
            lsWorkers = args.lsWorkers

        if args.opensearchPrimaryMode not in DATABASE_MODE_ENUMS.keys():
            raise Exception(f'"{args.opensearchPrimaryMode}" is not valid for --opensearch')

        if args.opensearchSecondaryMode and (args.opensearchSecondaryMode not in DATABASE_MODE_ENUMS.keys()):
            raise Exception(f'"{args.opensearchSecondaryMode}" is not valid for --opensearch-secondary')

        opensearchPrimaryMode = DatabaseMode.OpenSearchLocal
        opensearchPrimaryUrl = 'http://opensearch:9200'
        opensearchPrimarySslVerify = False
        opensearchPrimaryLabel = 'local OpenSearch'
        opensearchSecondaryMode = DatabaseMode.DatabaseUnset
        opensearchSecondaryUrl = ''
        opensearchSecondarySslVerify = False
        opensearchSecondaryLabel = 'remote OpenSearch'
        dashboardsUrl = 'http://dashboards:5601/dashboards'
        logstashHost = 'logstash:5044'
        indexSnapshotCompressed = False
        malcolmProfile = (
            PROFILE_MALCOLM
            if InstallerYesOrNo(
                'Run with Malcolm (all containers) or Hedgehog (capture only) profile?',
                default=args.malcolmProfile,
                yesLabel='Malcolm',
                noLabel='Hedgehog',
            )
            else PROFILE_HEDGEHOG
        )

        if (malcolmProfile == PROFILE_MALCOLM) and InstallerYesOrNo(
            'Should Malcolm use and maintain its own OpenSearch instance?',
            default=DATABASE_MODE_ENUMS[args.opensearchPrimaryMode] == DatabaseMode.OpenSearchLocal,
        ):
            opensearchPrimaryMode = DatabaseMode.OpenSearchLocal

        else:
            databaseModeChoice = ''
            allowedDatabaseModes = {
                DATABASE_MODE_LABELS[DatabaseMode.OpenSearchLocal]: [DatabaseMode.OpenSearchLocal, 'local OpenSearch'],
                DATABASE_MODE_LABELS[DatabaseMode.OpenSearchRemote]: [
                    DatabaseMode.OpenSearchRemote,
                    'remote OpenSearch',
                ],
                DATABASE_MODE_LABELS[DatabaseMode.ElasticsearchRemote]: [
                    DatabaseMode.ElasticsearchRemote,
                    'remote Elasticsearch',
                ],
            }
            if malcolmProfile != PROFILE_MALCOLM:
                del allowedDatabaseModes[DATABASE_MODE_LABELS[DatabaseMode.OpenSearchLocal]]
            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid primary document store mode')
            while databaseModeChoice not in list(allowedDatabaseModes.keys()) and loopBreaker.increment():
                databaseModeChoice = InstallerChooseOne(
                    'Select primary Malcolm document store',
                    choices=[
                        (x, allowedDatabaseModes[x][1], x == DATABASE_MODE_LABELS[DatabaseMode.OpenSearchLocal])
                        for x in list(allowedDatabaseModes.keys())
                    ],
                )
            opensearchPrimaryMode = allowedDatabaseModes[databaseModeChoice][0]
            opensearchPrimaryLabel = allowedDatabaseModes[databaseModeChoice][1]

        if opensearchPrimaryMode in (DatabaseMode.OpenSearchRemote, DatabaseMode.ElasticsearchRemote):
            loopBreaker = CountUntilException(MaxAskForValueCount, f'Invalid {opensearchPrimaryLabel} URL')
            opensearchPrimaryUrl = ''
            while (len(opensearchPrimaryUrl) <= 1) and loopBreaker.increment():
                opensearchPrimaryUrl = InstallerAskForString(
                    f'Enter primary {opensearchPrimaryLabel} connection URL (e.g., https://192.168.1.123:9200)',
                    default=args.opensearchPrimaryUrl,
                )
            opensearchPrimarySslVerify = opensearchPrimaryUrl.lower().startswith('https') and InstallerYesOrNo(
                f'Require SSL certificate validation for communication with {opensearchPrimaryLabel} instance?',
                default=args.opensearchPrimarySslVerify,
            )
        else:
            indexSnapshotCompressed = InstallerYesOrNo(
                f'Compress {opensearchPrimaryLabel} index snapshots?',
                default=args.indexSnapshotCompressed,
            )

        if opensearchPrimaryMode == DatabaseMode.ElasticsearchRemote:
            loopBreaker = CountUntilException(MaxAskForValueCount, f'Invalid Kibana connection URL')
            dashboardsUrl = ''
            while (len(dashboardsUrl) <= 1) and loopBreaker.increment():
                dashboardsUrl = InstallerAskForString(
                    f'Enter Kibana connection URL (e.g., https://192.168.1.123:5601)',
                    default=args.dashboardsUrl,
                )

        if malcolmProfile != PROFILE_MALCOLM:
            loopBreaker = CountUntilException(MaxAskForValueCount, f'Invalid Logstash host and port')
            logstashHost = ''
            while (len(logstashHost) <= 1) and loopBreaker.increment():
                logstashHost = InstallerAskForString(
                    f'Enter Logstash host and port (e.g., 192.168.1.123:5044)',
                    default=args.logstashHost,
                )
            pcapNodeHost = InstallerAskForString(
                f"Enter this node's hostname or IP to associate with network traffic metadata",
                default=args.pcapNodeHost,
            )
            if not pcapNodeHost and not InstallerYesOrNo(
                f'Node hostname or IP is required for Arkime session retrieval under the {malcolmProfile} profile. Are you sure?',
                default=False,
            ):
                pcapNodeHost = InstallerAskForString(
                    f"Enter this node's hostname or IP to associate with network traffic metadata",
                    default=args.pcapNodeHost,
                )

        if (malcolmProfile == PROFILE_MALCOLM) and InstallerYesOrNo(
            'Forward Logstash logs to a secondary remote document store?',
            default=(
                DATABASE_MODE_ENUMS[args.opensearchSecondaryMode]
                in (DatabaseMode.OpenSearchRemote, DatabaseMode.ElasticsearchRemote)
            ),
        ):
            databaseModeChoice = ''
            allowedDatabaseModes = {
                DATABASE_MODE_LABELS[DatabaseMode.OpenSearchRemote]: [
                    DatabaseMode.OpenSearchRemote,
                    'remote OpenSearch',
                ],
                DATABASE_MODE_LABELS[DatabaseMode.ElasticsearchRemote]: [
                    DatabaseMode.ElasticsearchRemote,
                    'remote Elasticsearch',
                ],
            }
            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid secondary document store mode')
            while databaseModeChoice not in list(allowedDatabaseModes.keys()) and loopBreaker.increment():
                databaseModeChoice = InstallerChooseOne(
                    'Select secondary Malcolm document store',
                    choices=[
                        (x, allowedDatabaseModes[x][1], x == args.opensearchSecondaryMode)
                        for x in list(allowedDatabaseModes.keys())
                    ],
                )
            opensearchSecondaryMode = allowedDatabaseModes[databaseModeChoice][0]
            opensearchSecondaryLabel = allowedDatabaseModes[databaseModeChoice][1]

        if opensearchSecondaryMode in (DatabaseMode.OpenSearchRemote, DatabaseMode.ElasticsearchRemote):
            loopBreaker = CountUntilException(MaxAskForValueCount, f'Invalid {opensearchSecondaryLabel} URL')
            opensearchSecondaryUrl = ''
            while (len(opensearchSecondaryUrl) <= 1) and loopBreaker.increment():
                opensearchSecondaryUrl = InstallerAskForString(
                    f'Enter secondary {opensearchSecondaryLabel} connection URL (e.g., https://192.168.1.123:9200)',
                    default=args.opensearchSecondaryUrl,
                )
            opensearchSecondarySslVerify = opensearchSecondaryUrl.lower().startswith('https') and InstallerYesOrNo(
                f'Require SSL certificate validation for communication with secondary {opensearchSecondaryLabel} instance?',
                default=args.opensearchSecondarySslVerify,
            )

        if (opensearchPrimaryMode in (DatabaseMode.OpenSearchRemote, DatabaseMode.ElasticsearchRemote)) or (
            opensearchSecondaryMode in (DatabaseMode.OpenSearchRemote, DatabaseMode.ElasticsearchRemote)
        ):
            InstallerDisplayMessage(
                f'You must run auth_setup after {ScriptName} to store data store connection credentials.',
            )

        if malcolmProfile == PROFILE_MALCOLM:
            loopBreaker = CountUntilException(
                MaxAskForValueCount,
                f'Invalid {"OpenSearch/" if opensearchPrimaryMode == DatabaseMode.OpenSearchLocal else ""}Logstash memory setting(s)',
            )
            while (
                not InstallerYesOrNo(
                    f'Setting {osMemory} for OpenSearch and {lsMemory} for Logstash. Is this OK?'
                    if opensearchPrimaryMode == DatabaseMode.OpenSearchLocal
                    else f'Setting {lsMemory} for Logstash. Is this OK?',
                    default=True,
                )
                and loopBreaker.increment()
            ):
                if opensearchPrimaryMode == DatabaseMode.OpenSearchLocal:
                    osMemory = InstallerAskForString('Enter memory for OpenSearch (e.g., 16g, 9500m, etc.)')
                lsMemory = InstallerAskForString('Enter memory for Logstash (e.g., 4g, 2500m, etc.)')

            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid Logstash worker setting(s)')
            while (
                (not str(lsWorkers).isdigit())
                or (
                    not InstallerYesOrNo(
                        f'Setting {lsWorkers} workers for Logstash pipelines. Is this OK?', default=True
                    )
                )
            ) and loopBreaker.increment():
                lsWorkers = InstallerAskForString('Enter number of Logstash workers (e.g., 4, 8, etc.)')

        restartMode = None
        allowedRestartModes = ('no', 'on-failure', 'always', 'unless-stopped')
        if (self.orchMode is OrchestrationFramework.DOCKER_COMPOSE) and InstallerYesOrNo(
            'Restart Malcolm upon system or Docker daemon restart?', default=args.malcolmAutoRestart
        ):
            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid restart mode')
            while restartMode not in allowedRestartModes and loopBreaker.increment():
                restartMode = InstallerChooseOne(
                    'Select Malcolm restart behavior',
                    choices=[(x, '', x == 'unless-stopped') for x in allowedRestartModes],
                )
        else:
            restartMode = 'no'
        if restartMode == 'no':
            restartMode = '"no"'

        if malcolmProfile == PROFILE_MALCOLM:
            nginxSSL = InstallerYesOrNo('Require encrypted HTTPS connections?', default=args.nginxSSL)
            if (not nginxSSL) and (not args.acceptDefaultsNonInteractive):
                nginxSSL = not InstallerYesOrNo(
                    'Unencrypted connections are NOT recommended. Are you sure?', default=False
                )
        else:
            nginxSSL = True

        behindReverseProxy = False
        dockerNetworkExternalName = ""
        traefikLabels = False
        traefikHost = ""
        traefikOpenSearchHost = ""
        traefikEntrypoint = ""
        traefikResolver = ""

        behindReverseProxy = (self.orchMode is OrchestrationFramework.KUBERNETES) or (
            (malcolmProfile == PROFILE_MALCOLM)
            and InstallerYesOrNo(
                'Will Malcolm be running behind another reverse proxy (Traefik, Caddy, etc.)?',
                default=args.behindReverseProxy or (not nginxSSL),
            )
        )

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            if behindReverseProxy:
                traefikLabels = InstallerYesOrNo('Configure labels for Traefik?', default=bool(args.traefikHost))
                if traefikLabels:
                    loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid Traefik request domain')
                    while (len(traefikHost) <= 1) and loopBreaker.increment():
                        traefikHost = InstallerAskForString(
                            'Enter request domain (host header value) for Malcolm interface Traefik router (e.g., malcolm.example.org)',
                            default=args.traefikHost,
                        )
                    if opensearchPrimaryMode == DatabaseMode.OpenSearchLocal:
                        loopBreaker = CountUntilException(
                            MaxAskForValueCount, 'Invalid Traefik OpenSearch request domain'
                        )
                        while (
                            (len(traefikOpenSearchHost) <= 1) or (traefikOpenSearchHost == traefikHost)
                        ) and loopBreaker.increment():
                            traefikOpenSearchHost = InstallerAskForString(
                                f'Enter request domain (host header value) for OpenSearch Traefik router (e.g., opensearch.{traefikHost})',
                                default=args.traefikOpenSearchHost,
                            )
                    loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid Traefik router entrypoint')
                    while (len(traefikEntrypoint) <= 1) and loopBreaker.increment():
                        traefikEntrypoint = InstallerAskForString(
                            'Enter Traefik router entrypoint (e.g., websecure)',
                            default=args.traefikEntrypoint,
                        )
                    loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid Traefik router resolver')
                    while (len(traefikResolver) <= 1) and loopBreaker.increment():
                        traefikResolver = InstallerAskForString(
                            'Enter Traefik router resolver (e.g., myresolver)',
                            default=args.traefikResolver,
                        )

            dockerNetworkExternalName = InstallerAskForString(
                'Specify external Docker network name (or leave blank for default networking)',
                default=args.dockerNetworkName,
            )

        allowedAuthModes = {
            'Basic': 'true',
            'Lightweight Directory Access Protocol (LDAP)': 'false',
            'None': 'no_authentication',
        }
        authMode = None if (malcolmProfile == PROFILE_MALCOLM) else 'Basic'
        loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid authentication method')
        while authMode not in list(allowedAuthModes.keys()) and loopBreaker.increment():
            authMode = InstallerChooseOne(
                'Select authentication method',
                choices=[
                    (x, '', x == ('Lightweight Directory Access Protocol (LDAP)' if args.authModeLDAP else 'Basic'))
                    for x in list(allowedAuthModes.keys())
                ],
            )

        ldapStartTLS = False
        ldapServerTypeDefault = args.ldapServerType if args.ldapServerType else 'winldap'
        ldapServerType = ldapServerTypeDefault
        if 'ldap' in authMode.lower():
            allowedLdapModes = ('winldap', 'openldap')
            ldapServerType = args.ldapServerType if args.ldapServerType else None
            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid LDAP server compatibility type')
            while ldapServerType not in allowedLdapModes and loopBreaker.increment():
                ldapServerType = InstallerChooseOne(
                    'Select LDAP server compatibility type',
                    choices=[(x, '', x == ldapServerTypeDefault) for x in allowedLdapModes],
                )
            ldapStartTLS = InstallerYesOrNo(
                'Use StartTLS (rather than LDAPS) for LDAP connection security?', default=args.ldapStartTLS
            )
            try:
                with open(
                    os.path.join(os.path.realpath(os.path.join(ScriptPath, "..")), ".ldap_config_defaults"), "w"
                ) as ldapDefaultsFile:
                    print(f"LDAP_SERVER_TYPE='{ldapServerType}'", file=ldapDefaultsFile)
                    print(
                        f"LDAP_PROTO='{'ldap://' if ldapStartTLS else 'ldaps://'}'",
                        file=ldapDefaultsFile,
                    )
                    print(f"LDAP_PORT='{3268 if ldapStartTLS else 3269}'", file=ldapDefaultsFile)
            except Exception:
                pass

        # directories for data volume mounts (PCAP storage, Zeek log storage, OpenSearch indexes, etc.)
        indexDir = './opensearch'
        indexDirDefault = os.path.join(malcolm_install_path, indexDir)
        indexDirFull = os.path.realpath(indexDirDefault)

        indexSnapshotCompressed = False
        indexSnapshotDir = './opensearch-backup'
        indexSnapshotDirDefault = os.path.join(malcolm_install_path, indexSnapshotDir)
        indexSnapshotDirFull = os.path.realpath(indexSnapshotDirDefault)

        pcapDir = './pcap'
        pcapDirDefault = os.path.join(malcolm_install_path, pcapDir)
        pcapDirFull = os.path.realpath(pcapDirDefault)

        suricataLogDir = './suricata-logs'
        suricataLogDirDefault = os.path.join(malcolm_install_path, suricataLogDir)
        suricataLogDirFull = os.path.realpath(suricataLogDirDefault)

        zeekLogDir = './zeek-logs'
        zeekLogDirDefault = os.path.join(malcolm_install_path, zeekLogDir)
        zeekLogDirFull = os.path.realpath(zeekLogDirDefault)

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            if not InstallerYesOrNo(
                f'Store {"PCAP, log and index" if (malcolmProfile == PROFILE_MALCOLM) else "PCAP and log"} files locally under {malcolm_install_path}?',
                default=not args.acceptDefaultsNonInteractive,
            ):
                # PCAP directory
                if not InstallerYesOrNo(
                    'Store PCAP files locally in {}?'.format(pcapDirDefault),
                    default=not bool(args.pcapDir),
                ):
                    loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid PCAP directory')
                    while loopBreaker.increment():
                        pcapDir = InstallerAskForString('Enter PCAP directory', default=args.pcapDir)
                        if (len(pcapDir) > 1) and os.path.isdir(pcapDir):
                            pcapDirFull = os.path.realpath(pcapDir)
                            pcapDir = (
                                f"./{os.path.relpath(pcapDirDefault, malcolm_install_path)}"
                                if same_file_or_dir(pcapDirDefault, pcapDirFull)
                                else pcapDirFull
                            )
                            break

                # Zeek log directory
                if not InstallerYesOrNo(
                    'Store Zeek logs locally in {}?'.format(zeekLogDirDefault),
                    default=not bool(args.zeekLogDir),
                ):
                    loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid Zeek directory')
                    while loopBreaker.increment():
                        zeekLogDir = InstallerAskForString('Enter Zeek log directory', default=args.zeekLogDir)
                        if (len(zeekLogDir) > 1) and os.path.isdir(zeekLogDir):
                            zeekLogDirFull = os.path.realpath(zeekLogDir)
                            zeekLogDir = (
                                f"./{os.path.relpath(zeekLogDirDefault, malcolm_install_path)}"
                                if same_file_or_dir(zeekLogDirDefault, zeekLogDirFull)
                                else zeekLogDirFull
                            )
                            break

                # Suricata log directory
                if not InstallerYesOrNo(
                    'Store Suricata logs locally in {}?'.format(suricataLogDirDefault),
                    default=not bool(args.suricataLogDir),
                ):
                    loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid Suricata directory')
                    while loopBreaker.increment():
                        suricataLogDir = InstallerAskForString(
                            'Enter Suricata log directory', default=args.suricataLogDir
                        )
                        if (len(suricataLogDir) > 1) and os.path.isdir(suricataLogDir):
                            suricataLogDirFull = os.path.realpath(suricataLogDir)
                            suricataLogDir = (
                                f"./{os.path.relpath(suricataLogDirDefault, malcolm_install_path)}"
                                if same_file_or_dir(suricataLogDirDefault, suricataLogDirFull)
                                else suricataLogDirFull
                            )
                            break

                if (malcolmProfile == PROFILE_MALCOLM) and (opensearchPrimaryMode == DatabaseMode.OpenSearchLocal):
                    # opensearch index directory
                    if not InstallerYesOrNo(
                        'Store OpenSearch indices locally in {}?'.format(indexDirDefault),
                        default=not bool(args.indexDir),
                    ):
                        loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid OpenSearch index directory')
                        while loopBreaker.increment():
                            indexDir = InstallerAskForString('Enter OpenSearch index directory', default=args.indexDir)
                            if (len(indexDir) > 1) and os.path.isdir(indexDir):
                                indexDirFull = os.path.realpath(indexDir)
                                indexDir = (
                                    f"./{os.path.relpath(indexDirDefault, malcolm_install_path)}"
                                    if same_file_or_dir(indexDirDefault, indexDirFull)
                                    else indexDirFull
                                )
                                break

                    # opensearch snapshot repository directory and compression
                    if not InstallerYesOrNo(
                        'Store OpenSearch index snapshots locally in {}?'.format(indexSnapshotDirDefault),
                        default=not bool(args.indexSnapshotDir),
                    ):
                        loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid OpenSearch snapshots directory')
                        while loopBreaker.increment():
                            indexSnapshotDir = InstallerAskForString(
                                'Enter OpenSearch index snapshot directory', default=args.indexSnapshotDir
                            )
                            if (len(indexSnapshotDir) > 1) and os.path.isdir(indexSnapshotDir):
                                indexSnapshotDirFull = os.path.realpath(indexSnapshotDir)
                                indexSnapshotDir = (
                                    f"./{os.path.relpath(indexSnapshotDirDefault, malcolm_install_path)}"
                                    if same_file_or_dir(indexSnapshotDirDefault, indexSnapshotDirFull)
                                    else indexSnapshotDirFull
                                )
                                break

            # make sure paths specified (and their necessary children) exist
            for pathToCreate in (
                indexDirFull,
                indexSnapshotDirFull,
                os.path.join(pcapDirFull, 'processed'),
                os.path.join(pcapDirFull, os.path.join('upload', os.path.join('tmp', 'spool'))),
                os.path.join(pcapDirFull, os.path.join('upload', 'variants')),
                os.path.join(suricataLogDirFull, 'live'),
                os.path.join(zeekLogDirFull, 'current'),
                os.path.join(zeekLogDirFull, 'live'),
                os.path.join(zeekLogDirFull, 'upload'),
                os.path.join(zeekLogDirFull, os.path.join('extract_files', 'preserved')),
                os.path.join(zeekLogDirFull, os.path.join('extract_files', 'quarantine')),
            ):
                try:
                    if args.debug:
                        eprint(f"Creating {pathToCreate}")
                    pathlib.Path(pathToCreate).mkdir(parents=True, exist_ok=True)
                    if (
                        ((self.platform == PLATFORM_LINUX) or (self.platform == PLATFORM_MAC))
                        and (self.scriptUser == "root")
                        and (getpwuid(os.stat(pathToCreate).st_uid).pw_name == self.scriptUser)
                    ):
                        if args.debug:
                            eprint(f"Setting permissions of {pathToCreate} to {puid}:{pgid}")
                        # change ownership of newly-created directory to match puid/pgid
                        os.chown(pathToCreate, int(puid), int(pgid))
                except Exception as e:
                    eprint(f"Creating {pathToCreate} failed: {e}")

        # storage management (deleting oldest indices and/or PCAP files)
        indexPruneSizeLimit = '0'
        indexPruneNameSort = False
        arkimeManagePCAP = False

        if InstallerYesOrNo(
            'Should Malcolm delete the oldest database indices and/or PCAP files based on available storage?'
            if ((opensearchPrimaryMode == DatabaseMode.OpenSearchLocal) and (malcolmProfile == PROFILE_MALCOLM))
            else 'Should Arkime delete PCAP files based on available storage (see https://arkime.com/faq#pcap-deletion)?',
            default=args.arkimeManagePCAP or bool(args.indexPruneSizeLimit),
        ):
            # delete oldest indexes based on index pattern size
            if (
                (malcolmProfile == PROFILE_MALCOLM)
                and (opensearchPrimaryMode == DatabaseMode.OpenSearchLocal)
                and InstallerYesOrNo(
                    'Delete the oldest indices when the database exceeds a certain size?',
                    default=bool(args.indexPruneSizeLimit),
                )
            ):
                indexPruneSizeLimit = ''
                loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid index threshold')
                while (
                    (not re.match(r'^\d+(\.\d+)?\s*[kmgtp%]?b?$', indexPruneSizeLimit, flags=re.IGNORECASE))
                    and (indexPruneSizeLimit != '0')
                    and loopBreaker.increment()
                ):
                    indexPruneSizeLimit = InstallerAskForString(
                        'Enter index threshold (e.g., 250GB, 1TB, 60%, etc.)', default=args.indexPruneSizeLimit
                    )
                indexPruneNameSort = InstallerYesOrNo(
                    'Determine oldest indices by name (instead of creation time)?', default=True
                )

            # let Arkime delete old PCAP files based on available storage
            arkimeManagePCAP = (
                (opensearchPrimaryMode != DatabaseMode.OpenSearchLocal)
                or (malcolmProfile != PROFILE_MALCOLM)
                or InstallerYesOrNo(
                    'Should Arkime delete PCAP files based on available storage (see https://arkime.com/faq#pcap-deletion)?',
                    default=args.arkimeManagePCAP,
                )
            )

        autoSuricata = InstallerYesOrNo(
            'Automatically analyze all PCAP files with Suricata?', default=args.autoSuricata
        )
        suricataRuleUpdate = autoSuricata and InstallerYesOrNo(
            'Download updated Suricata signatures periodically?', default=args.suricataRuleUpdate
        )
        autoZeek = InstallerYesOrNo('Automatically analyze all PCAP files with Zeek?', default=args.autoZeek)

        zeekIcs = InstallerYesOrNo(
            'Is Malcolm being used to monitor an Operational Technology/Industrial Control Systems (OT/ICS) network?',
            default=args.zeekIcs,
        )

        zeekICSBestGuess = (
            autoZeek
            and zeekIcs
            and InstallerYesOrNo(
                'Should Malcolm use "best guess" to identify potential OT/ICS traffic with Zeek?',
                default=args.zeekICSBestGuess,
            )
        )

        reverseDns = (malcolmProfile == PROFILE_MALCOLM) and InstallerYesOrNo(
            'Perform reverse DNS lookup locally for source and destination IP addresses in logs?',
            default=args.reverseDns,
        )
        autoOui = (malcolmProfile == PROFILE_MALCOLM) and InstallerYesOrNo(
            'Perform hardware vendor OUI lookups for MAC addresses?', default=args.autoOui
        )
        autoFreq = (malcolmProfile == PROFILE_MALCOLM) and InstallerYesOrNo(
            'Perform string randomness scoring on some fields?', default=args.autoFreq
        )

        openPortsSelection = (
            'c'
            if (args.exposeLogstash or args.exposeOpenSearch or args.exposeFilebeatTcp or args.exposeSFTP)
            else 'unset'
        )
        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            if malcolmProfile == PROFILE_MALCOLM:
                arkimeViewerOpen = False
                openPortsOptions = ('no', 'yes', 'customize')
                loopBreaker = CountUntilException(MaxAskForValueCount)
                while openPortsSelection not in [x[0] for x in openPortsOptions] and loopBreaker.increment():
                    openPortsSelection = InstallerChooseOne(
                        'Should Malcolm accept logs and metrics from a Hedgehog Linux sensor or other forwarder?',
                        choices=[(x, '', x == openPortsOptions[0]) for x in openPortsOptions],
                    )[0]
                if openPortsSelection == 'n':
                    opensearchOpen = False
                    logstashOpen = False
                    filebeatTcpOpen = False
                elif openPortsSelection == 'y':
                    opensearchOpen = True
                    logstashOpen = True
                    filebeatTcpOpen = True
                else:
                    openPortsSelection = 'c'
                    opensearchOpen = (opensearchPrimaryMode == DatabaseMode.OpenSearchLocal) and InstallerYesOrNo(
                        'Expose OpenSearch port to external hosts?', default=args.exposeOpenSearch
                    )
                    logstashOpen = InstallerYesOrNo(
                        'Expose Logstash port to external hosts?', default=args.exposeLogstash
                    )
                    filebeatTcpOpen = InstallerYesOrNo(
                        'Expose Filebeat TCP port to external hosts?', default=args.exposeFilebeatTcp
                    )
            else:
                opensearchOpen = False
                openPortsSelection = 'n'
                logstashOpen = False
                filebeatTcpOpen = False
                arkimeViewerOpen = InstallerYesOrNo(
                    'Expose Arkime viewer to external hosts for PCAP payload retrieval?',
                    default=args.exposeArkimeViewer,
                )
        else:
            opensearchOpen = opensearchPrimaryMode == DatabaseMode.OpenSearchLocal
            openPortsSelection = 'y'
            logstashOpen = True
            filebeatTcpOpen = True
            arkimeViewerOpen = malcolmProfile == PROFILE_HEDGEHOG

        filebeatTcpFormat = 'json'
        filebeatTcpSourceField = 'message'
        filebeatTcpTargetField = 'miscbeat'
        filebeatTcpDropField = filebeatTcpSourceField
        filebeatTcpTag = '_malcolm_beats'
        if (
            filebeatTcpOpen
            and (openPortsSelection == 'c')
            and not InstallerYesOrNo('Use default field values for Filebeat TCP listener?', default=True)
        ):
            allowedFilebeatTcpFormats = ('json', 'raw')
            filebeatTcpFormat = 'unset'
            loopBreaker = CountUntilException(MaxAskForValueCount, f'Invalid log format')
            while filebeatTcpFormat not in allowedFilebeatTcpFormats and loopBreaker.increment():
                filebeatTcpFormat = InstallerChooseOne(
                    'Select log format for messages sent to Filebeat TCP listener',
                    choices=[(x, '', x == allowedFilebeatTcpFormats[0]) for x in allowedFilebeatTcpFormats],
                )
            if filebeatTcpFormat == 'json':
                filebeatTcpSourceField = InstallerAskForString(
                    'Source field to parse for messages sent to Filebeat TCP listener',
                    default=filebeatTcpSourceField,
                )
                filebeatTcpTargetField = InstallerAskForString(
                    'Target field under which to store decoded JSON fields for messages sent to Filebeat TCP listener',
                    default=filebeatTcpTargetField,
                )
                filebeatTcpDropField = InstallerAskForString(
                    'Field to drop from events sent to Filebeat TCP listener',
                    default=filebeatTcpSourceField,
                )
            filebeatTcpTag = InstallerAskForString(
                'Tag to apply to messages sent to Filebeat TCP listener',
                default=filebeatTcpTag,
            )

        sftpOpen = (
            (self.orchMode is OrchestrationFramework.DOCKER_COMPOSE)
            and (malcolmProfile == PROFILE_MALCOLM)
            and (openPortsSelection == 'c')
            and InstallerYesOrNo('Expose SFTP server (for PCAP upload) to external hosts?', default=args.exposeSFTP)
        )

        # input file extraction parameters
        allowedFileCarveModes = ('none', 'known', 'mapped', 'all', 'interesting')
        allowedFilePreserveModes = ('quarantined', 'all', 'none')

        fileCarveMode = None
        fileCarveModeDefault = args.fileCarveMode.lower() if args.fileCarveMode else None
        filePreserveMode = None
        filePreserveModeDefault = args.filePreserveMode.lower() if args.filePreserveMode else None
        vtotApiKey = '0'
        yaraScan = False
        capaScan = False
        clamAvScan = False
        fileScanRuleUpdate = False
        fileCarveHttpServer = False
        fileCarveHttpServeEncryptKey = ''

        if InstallerYesOrNo('Enable file extraction with Zeek?', default=bool(fileCarveModeDefault)):
            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid file extraction behavior')
            while fileCarveMode not in allowedFileCarveModes and loopBreaker.increment():
                fileCarveMode = InstallerChooseOne(
                    'Select file extraction behavior',
                    choices=[
                        (x, '', x == fileCarveModeDefault if fileCarveModeDefault else allowedFileCarveModes[0])
                        for x in allowedFileCarveModes
                    ],
                )
            if fileCarveMode and (fileCarveMode != 'none'):
                loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid file preservation behavior')
                while filePreserveMode not in allowedFilePreserveModes and loopBreaker.increment():
                    filePreserveMode = InstallerChooseOne(
                        'Select file preservation behavior',
                        choices=[
                            (
                                x,
                                '',
                                x == filePreserveModeDefault
                                if filePreserveModeDefault
                                else allowedFilePreserveModes[0],
                            )
                            for x in allowedFilePreserveModes
                        ],
                    )
                fileCarveHttpServer = (malcolmProfile == PROFILE_MALCOLM) and InstallerYesOrNo(
                    'Expose web interface for downloading preserved files?', default=args.fileCarveHttpServer
                )
                if fileCarveHttpServer:
                    fileCarveHttpServeEncryptKey = InstallerAskForString(
                        'Enter AES-256-CBC encryption password for downloaded preserved files (or leave blank for unencrypted)',
                        default=args.fileCarveHttpServeEncryptKey,
                    )
                if fileCarveMode is not None:
                    if InstallerYesOrNo('Scan extracted files with ClamAV?', default=args.clamAvScan):
                        clamAvScan = True
                    if InstallerYesOrNo('Scan extracted files with Yara?', default=args.yaraScan):
                        yaraScan = True
                    if InstallerYesOrNo('Scan extracted PE files with Capa?', default=args.capaScan):
                        capaScan = True
                    if InstallerYesOrNo(
                        'Lookup extracted file hashes with VirusTotal?', default=(len(args.vtotApiKey) > 1)
                    ):
                        loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid VirusTotal API key')
                        while (len(vtotApiKey) <= 1) and loopBreaker.increment():
                            vtotApiKey = InstallerAskForString('Enter VirusTotal API key', default=args.vtotApiKey)
                    fileScanRuleUpdate = InstallerYesOrNo(
                        'Download updated file scanner signatures periodically?', default=args.fileScanRuleUpdate
                    )

        if fileCarveMode not in allowedFileCarveModes:
            fileCarveMode = allowedFileCarveModes[0]
        if filePreserveMode not in allowedFileCarveModes:
            filePreserveMode = allowedFilePreserveModes[0]
        if (vtotApiKey is None) or (len(vtotApiKey) <= 1):
            vtotApiKey = '0'

        # NetBox
        netboxEnabled = (malcolmProfile == PROFILE_MALCOLM) and InstallerYesOrNo(
            'Should Malcolm run and maintain an instance of NetBox, an infrastructure resource modeling tool?',
            default=args.netboxEnabled,
        )
        netboxLogstashEnrich = netboxEnabled and InstallerYesOrNo(
            'Should Malcolm enrich network traffic using NetBox?',
            default=args.netboxLogstashEnrich,
        )
        netboxLogstashAutoPopulate = (
            netboxEnabled
            and InstallerYesOrNo(
                'Should Malcolm automatically populate NetBox inventory based on observed network traffic?',
                default=args.netboxLogstashAutoPopulate,
            )
            and (
                InstallerYesOrNo(
                    "Autopopulating NetBox's inventory is not recommended. Are you sure?",
                    default=args.netboxLogstashAutoPopulate,
                )
            )
        )
        netboxSiteName = (
            InstallerAskForString(
                'Specify default NetBox site name',
                default=args.netboxSiteName,
            )
            if netboxEnabled
            else ''
        )
        if len(netboxSiteName) == 0:
            netboxSiteName = 'Malcolm'

        # input packet capture parameters
        pcapNetSniff = False
        pcapTcpDump = False
        liveZeek = False
        liveSuricata = False
        pcapIface = 'lo'
        tweakIface = False
        pcapFilter = ''
        captureSelection = (
            'c'
            if (
                args.pcapNetSniff
                or args.pcapTcpDump
                or args.liveZeek
                or args.liveSuricata
                or (malcolmProfile == PROFILE_HEDGEHOG)
            )
            else 'unset'
        )

        # if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
        captureOptions = ('no', 'yes', 'customize')
        loopBreaker = CountUntilException(MaxAskForValueCount)
        while captureSelection not in [x[0] for x in captureOptions] and loopBreaker.increment():
            captureSelection = InstallerChooseOne(
                'Should Malcolm capture live network traffic?',
                choices=[(x, '', x == captureOptions[0]) for x in captureOptions],
            )[0]
        if captureSelection == 'y':
            pcapNetSniff = True
            liveSuricata = True
            liveZeek = True
        elif captureSelection == 'c':
            if InstallerYesOrNo(
                'Should Malcolm capture live network traffic to PCAP files for analysis with Arkime?',
                default=args.pcapNetSniff or args.pcapTcpDump or (malcolmProfile == PROFILE_HEDGEHOG),
            ):
                pcapNetSniff = InstallerYesOrNo('Capture packets using netsniff-ng?', default=args.pcapNetSniff)
                if not pcapNetSniff:
                    pcapTcpDump = InstallerYesOrNo('Capture packets using tcpdump?', default=args.pcapTcpDump)
            liveSuricata = InstallerYesOrNo(
                'Should Malcolm analyze live network traffic with Suricata?', default=args.liveSuricata
            )
            liveZeek = InstallerYesOrNo(
                'Should Malcolm analyze live network traffic with Zeek?', default=args.liveZeek
            )
            if pcapNetSniff or pcapTcpDump or liveZeek or liveSuricata:
                pcapFilter = InstallerAskForString(
                    'Capture filter (tcpdump-like filter expression; leave blank to capture all traffic)',
                    default=args.pcapFilter,
                )
                tweakIface = InstallerYesOrNo(
                    'Disable capture interface hardware offloading and adjust ring buffer sizes?',
                    default=args.tweakIface,
                )

        if pcapNetSniff or pcapTcpDump or liveZeek or liveSuricata:
            pcapIface = ''
            loopBreaker = CountUntilException(MaxAskForValueCount, 'Invalid capture interface(s)')
            while (len(pcapIface) <= 0) and loopBreaker.increment():
                pcapIface = InstallerAskForString(
                    'Specify capture interface(s) (comma-separated)', default=args.pcapIface
                )

        if (
            (malcolmProfile == PROFILE_HEDGEHOG)
            and (not pcapNetSniff)
            and (not pcapTcpDump)
            and (not liveZeek)
            and (not liveSuricata)
        ):
            InstallerDisplayMessage(
                f'Warning: Running with the {malcolmProfile} profile but no capture methods are enabled.',
            )

        dashboardsDarkMode = (
            (malcolmProfile == PROFILE_MALCOLM)
            and (opensearchPrimaryMode != DatabaseMode.ElasticsearchRemote)
            and InstallerYesOrNo('Enable dark mode for OpenSearch Dashboards?', default=args.dashboardsDarkMode)
        )

        # modify values in .env files in args.configDir

        # first, if the args.configDir is completely empty, then populate from defaults
        examplesConfigDir = os.path.join(malcolm_install_path, 'config')
        if (
            os.path.isdir(examplesConfigDir)
            and (not same_file_or_dir(examplesConfigDir, args.configDir))
            and (not os.listdir(args.configDir))
        ):
            for defaultEnvExampleFile in glob.glob(os.path.join(examplesConfigDir, '*.env.example')):
                shutil.copy2(defaultEnvExampleFile, args.configDir)

        # if a specific config/*.env file doesn't exist, use the *.example.env files as defaults
        for envExampleFile in glob.glob(os.path.join(args.configDir, '*.env.example')):
            envFile = envExampleFile[: -len('.example')]
            if not os.path.isfile(envFile):
                shutil.copyfile(envExampleFile, envFile)

        # define environment variables to be set in .env files
        EnvValue = namedtuple("EnvValue", ["envFile", "key", "value"], rename=False)

        EnvValues = [
            # Whether or not Arkime is allowed to delete uploaded/captured PCAP
            EnvValue(
                os.path.join(args.configDir, 'arkime.env'),
                'MANAGE_PCAP_FILES',
                TrueOrFalseNoQuote(arkimeManagePCAP),
            ),
            # authentication method: basic (true), ldap (false) or no_authentication
            EnvValue(
                os.path.join(args.configDir, 'auth-common.env'),
                'NGINX_BASIC_AUTH',
                allowedAuthModes.get(authMode, TrueOrFalseNoQuote(True)),
            ),
            # StartTLS vs. ldap:// or ldaps://
            EnvValue(
                os.path.join(args.configDir, 'auth-common.env'),
                'NGINX_LDAP_TLS_STUNNEL',
                TrueOrFalseNoQuote(('ldap' in authMode.lower()) and ldapStartTLS),
            ),
            # Logstash host and port
            EnvValue(
                os.path.join(args.configDir, 'beats-common.env'),
                'LOGSTASH_HOST',
                logstashHost,
            ),
            # OpenSearch Dashboards URL
            EnvValue(
                os.path.join(args.configDir, 'dashboards.env'),
                'DASHBOARDS_URL',
                dashboardsUrl,
            ),
            # turn on dark mode, or not
            EnvValue(
                os.path.join(args.configDir, 'dashboards-helper.env'),
                'DASHBOARDS_DARKMODE',
                TrueOrFalseNoQuote(dashboardsDarkMode),
            ),
            # OpenSearch index state management snapshot compression
            EnvValue(
                os.path.join(args.configDir, 'dashboards-helper.env'),
                'ISM_SNAPSHOT_COMPRESSED',
                TrueOrFalseNoQuote(indexSnapshotCompressed),
            ),
            # delete based on index pattern size
            EnvValue(
                os.path.join(args.configDir, 'dashboards-helper.env'),
                'OPENSEARCH_INDEX_SIZE_PRUNE_LIMIT',
                indexPruneSizeLimit,
            ),
            # delete based on index pattern size (sorted by name vs. creation time)
            EnvValue(
                os.path.join(args.configDir, 'dashboards-helper.env'),
                'OPENSEARCH_INDEX_SIZE_PRUNE_NAME_SORT',
                TrueOrFalseNoQuote(indexPruneNameSort),
            ),
            # expose a filebeat TCP input listener
            EnvValue(
                os.path.join(args.configDir, 'filebeat.env'),
                'FILEBEAT_TCP_LISTEN',
                TrueOrFalseNoQuote(filebeatTcpOpen),
            ),
            # log format expected for events sent to the filebeat TCP input listener
            EnvValue(
                os.path.join(args.configDir, 'filebeat.env'),
                'FILEBEAT_TCP_LOG_FORMAT',
                filebeatTcpFormat,
            ),
            # source field name to parse for events sent to the filebeat TCP input listener
            EnvValue(
                os.path.join(args.configDir, 'filebeat.env'),
                'FILEBEAT_TCP_PARSE_SOURCE_FIELD',
                filebeatTcpSourceField,
            ),
            # target field name to store decoded JSON fields for events sent to the filebeat TCP input listener
            EnvValue(
                os.path.join(args.configDir, 'filebeat.env'),
                'FILEBEAT_TCP_PARSE_TARGET_FIELD',
                filebeatTcpTargetField,
            ),
            # field to drop in events sent to the filebeat TCP input listener
            EnvValue(
                os.path.join(args.configDir, 'filebeat.env'),
                'FILEBEAT_TCP_PARSE_DROP_FIELD',
                filebeatTcpDropField,
            ),
            # tag to append to events sent to the filebeat TCP input listener
            EnvValue(
                os.path.join(args.configDir, 'filebeat.env'),
                'FILEBEAT_TCP_TAG',
                filebeatTcpTag,
            ),
            # logstash memory allowance
            EnvValue(
                os.path.join(args.configDir, 'logstash.env'),
                'LS_JAVA_OPTS',
                re.sub(r'(-Xm[sx])(\w+)', fr'\g<1>{lsMemory}', LOGSTASH_JAVA_OPTS_DEFAULT),
            ),
            # automatic local reverse dns lookup
            EnvValue(
                os.path.join(args.configDir, 'logstash.env'),
                'LOGSTASH_REVERSE_DNS',
                TrueOrFalseNoQuote(reverseDns),
            ),
            # automatic MAC OUI lookup
            EnvValue(
                os.path.join(args.configDir, 'logstash.env'),
                'LOGSTASH_OUI_LOOKUP',
                TrueOrFalseNoQuote(autoOui),
            ),
            # enrich network traffic metadata via NetBox API calls
            EnvValue(
                os.path.join(args.configDir, 'logstash.env'),
                'LOGSTASH_NETBOX_ENRICHMENT',
                TrueOrFalseNoQuote(netboxLogstashEnrich),
            ),
            # populate the NetBox inventory based on observed network traffic
            EnvValue(
                os.path.join(args.configDir, 'logstash.env'),
                'LOGSTASH_NETBOX_AUTO_POPULATE',
                TrueOrFalseNoQuote(netboxLogstashAutoPopulate),
            ),
            # logstash pipeline workers
            EnvValue(
                os.path.join(args.configDir, 'logstash.env'),
                'pipeline.workers',
                lsWorkers,
            ),
            # freq.py string randomness calculations
            EnvValue(
                os.path.join(args.configDir, 'lookup-common.env'),
                'FREQ_LOOKUP',
                TrueOrFalseNoQuote(autoFreq),
            ),
            # NetBox default site name
            EnvValue(
                os.path.join(args.configDir, 'netbox-common.env'),
                'NETBOX_DEFAULT_SITE',
                netboxSiteName,
            ),
            # enable/disable netbox
            EnvValue(
                os.path.join(args.configDir, 'netbox-common.env'),
                'NETBOX_DISABLED',
                TrueOrFalseNoQuote(not netboxEnabled),
            ),
            # enable/disable netbox (postgres)
            EnvValue(
                os.path.join(args.configDir, 'netbox-common.env'),
                'NETBOX_POSTGRES_DISABLED',
                TrueOrFalseNoQuote(not netboxEnabled),
            ),
            # enable/disable netbox (redis)
            EnvValue(
                os.path.join(args.configDir, 'netbox-common.env'),
                'NETBOX_REDIS_DISABLED',
                TrueOrFalseNoQuote(not netboxEnabled),
            ),
            # HTTPS (nginxSSL=True) vs unencrypted HTTP (nginxSSL=False)
            EnvValue(
                os.path.join(args.configDir, 'nginx.env'),
                'NGINX_SSL',
                TrueOrFalseNoQuote(nginxSSL),
            ),
            # OpenSearch primary instance is local vs. remote
            EnvValue(
                os.path.join(args.configDir, 'opensearch.env'),
                'OPENSEARCH_PRIMARY',
                DATABASE_MODE_LABELS[opensearchPrimaryMode],
            ),
            # OpenSearch primary instance URL
            EnvValue(
                os.path.join(args.configDir, 'opensearch.env'),
                'OPENSEARCH_URL',
                opensearchPrimaryUrl,
            ),
            # OpenSearch primary instance needs SSL verification
            EnvValue(
                os.path.join(args.configDir, 'opensearch.env'),
                'OPENSEARCH_SSL_CERTIFICATE_VERIFICATION',
                TrueOrFalseNoQuote(opensearchPrimarySslVerify),
            ),
            # OpenSearch secondary instance URL
            EnvValue(
                os.path.join(args.configDir, 'opensearch.env'),
                'OPENSEARCH_SECONDARY_URL',
                opensearchSecondaryUrl,
            ),
            # OpenSearch secondary instance needs SSL verification
            EnvValue(
                os.path.join(args.configDir, 'opensearch.env'),
                'OPENSEARCH_SECONDARY_SSL_CERTIFICATE_VERIFICATION',
                TrueOrFalseNoQuote(opensearchSecondarySslVerify),
            ),
            # OpenSearch secondary remote instance is enabled
            EnvValue(
                os.path.join(args.configDir, 'opensearch.env'),
                'OPENSEARCH_SECONDARY',
                DATABASE_MODE_LABELS[opensearchSecondaryMode],
            ),
            # OpenSearch memory allowance
            EnvValue(
                os.path.join(args.configDir, 'opensearch.env'),
                'OPENSEARCH_JAVA_OPTS',
                re.sub(r'(-Xm[sx])(\w+)', fr'\g<1>{osMemory}', OPENSEARCH_JAVA_OPTS_DEFAULT),
            ),
            # capture pcaps via netsniff-ng
            EnvValue(
                os.path.join(args.configDir, 'pcap-capture.env'),
                'PCAP_ENABLE_NETSNIFF',
                TrueOrFalseNoQuote(pcapNetSniff),
            ),
            # capture pcaps via tcpdump
            EnvValue(
                os.path.join(args.configDir, 'pcap-capture.env'),
                'PCAP_ENABLE_TCPDUMP',
                TrueOrFalseNoQuote(pcapTcpDump and (not pcapNetSniff)),
            ),
            # disable NIC hardware offloading features and adjust ring buffers
            EnvValue(
                os.path.join(args.configDir, 'pcap-capture.env'),
                'PCAP_IFACE_TWEAK',
                TrueOrFalseNoQuote(tweakIface),
            ),
            # capture interface(s)
            EnvValue(
                os.path.join(args.configDir, 'pcap-capture.env'),
                'PCAP_IFACE',
                pcapIface,
            ),
            # capture filter
            EnvValue(
                os.path.join(args.configDir, 'pcap-capture.env'),
                'PCAP_FILTER',
                pcapFilter,
            ),
            # process UID
            EnvValue(
                os.path.join(args.configDir, 'process.env'),
                'PUID',
                puid,
            ),
            # process GID
            EnvValue(
                os.path.join(args.configDir, 'process.env'),
                'PGID',
                pgid,
            ),
            # Malcolm run profile (malcolm vs. hedgehog)
            EnvValue(
                os.path.join(args.configDir, 'process.env'),
                PROFILE_KEY,
                malcolmProfile,
            ),
            # Suricata signature updates (via suricata-update)
            EnvValue(
                os.path.join(args.configDir, 'suricata.env'),
                'SURICATA_UPDATE_RULES',
                TrueOrFalseNoQuote(suricataRuleUpdate),
            ),
            # live traffic analysis with Suricata
            EnvValue(
                os.path.join(args.configDir, 'suricata-live.env'),
                'SURICATA_LIVE_CAPTURE',
                TrueOrFalseNoQuote(liveSuricata),
            ),
            # rotated captured PCAP analysis with Suricata (not live capture)
            EnvValue(
                os.path.join(args.configDir, 'suricata-offline.env'),
                'SURICATA_ROTATED_PCAP',
                TrueOrFalseNoQuote(autoSuricata and (not liveSuricata)),
            ),
            # automatic uploaded pcap analysis with suricata
            EnvValue(
                os.path.join(args.configDir, 'suricata-offline.env'),
                'SURICATA_AUTO_ANALYZE_PCAP_FILES',
                TrueOrFalseNoQuote(autoSuricata),
            ),
            # capture source "node name" for locally processed PCAP files
            EnvValue(
                os.path.join(args.configDir, 'upload-common.env'),
                'PCAP_NODE_NAME',
                pcapNodeName,
            ),
            # capture source "node host" for locally processed PCAP files
            EnvValue(
                os.path.join(args.configDir, 'upload-common.env'),
                'PCAP_NODE_HOST',
                pcapNodeHost,
            ),
            # zeek file extraction mode
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'ZEEK_EXTRACTOR_MODE',
                fileCarveMode,
            ),
            # zeek file preservation mode
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_PRESERVATION',
                filePreserveMode,
            ),
            # HTTP server for extracted files
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_HTTP_SERVER_ENABLE',
                TrueOrFalseNoQuote(fileCarveHttpServer),
            ),
            # encrypt HTTP server for extracted files
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_HTTP_SERVER_ENCRYPT',
                TrueOrFalseNoQuote(fileCarveHttpServer and (len(fileCarveHttpServeEncryptKey) > 0)),
            ),
            # key for encrypted HTTP-served extracted files (' -> '' for escaping in YAML)
            EnvValue(
                os.path.join(args.configDir, 'zeek-secret.env'),
                'EXTRACTED_FILE_HTTP_SERVER_KEY',
                fileCarveHttpServeEncryptKey,
            ),
            # virustotal API key
            EnvValue(
                os.path.join(args.configDir, 'zeek-secret.env'),
                'VTOT_API2_KEY',
                vtotApiKey,
            ),
            # file scanning via yara
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_ENABLE_YARA',
                TrueOrFalseNoQuote(yaraScan),
            ),
            # PE file scanning via capa
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_ENABLE_CAPA',
                TrueOrFalseNoQuote(capaScan),
            ),
            # file scanning via clamav
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_ENABLE_CLAMAV',
                TrueOrFalseNoQuote(clamAvScan),
            ),
            # rule updates (yara/capa via git, clamav via freshclam)
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_UPDATE_RULES',
                TrueOrFalseNoQuote(fileScanRuleUpdate),
            ),
            # disable/enable ICS analyzers
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'ZEEK_DISABLE_ICS_ALL',
                '' if zeekIcs else TrueOrFalseNoQuote(not zeekIcs),
            ),
            # disable/enable ICS best guess
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'ZEEK_DISABLE_BEST_GUESS_ICS',
                '' if zeekICSBestGuess else TrueOrFalseNoQuote(not zeekICSBestGuess),
            ),
            # live traffic analysis with Zeek
            EnvValue(
                os.path.join(args.configDir, 'zeek-live.env'),
                'ZEEK_LIVE_CAPTURE',
                TrueOrFalseNoQuote(liveZeek),
            ),
            # rotated captured PCAP analysis with Zeek (not live capture)
            EnvValue(
                os.path.join(args.configDir, 'zeek-offline.env'),
                'ZEEK_ROTATED_PCAP',
                TrueOrFalseNoQuote(autoZeek and (not liveZeek)),
            ),
            # automatic uploaded pcap analysis with Zeek
            EnvValue(
                os.path.join(args.configDir, 'zeek-offline.env'),
                'ZEEK_AUTO_ANALYZE_PCAP_FILES',
                TrueOrFalseNoQuote(autoZeek),
            ),
            # Use polling for file watching vs. native
            EnvValue(
                os.path.join(args.configDir, 'zeek.env'),
                'EXTRACTED_FILE_WATCHER_POLLING',
                TrueOrFalseNoQuote(self.orchMode is OrchestrationFramework.KUBERNETES),
            ),
            EnvValue(
                os.path.join(args.configDir, 'upload-common.env'),
                'PCAP_PIPELINE_POLLING',
                TrueOrFalseNoQuote(self.orchMode is OrchestrationFramework.KUBERNETES),
            ),
            EnvValue(
                os.path.join(args.configDir, 'filebeat.env'),
                'FILEBEAT_WATCHER_POLLING',
                TrueOrFalseNoQuote(self.orchMode is OrchestrationFramework.KUBERNETES),
            ),
        ]

        # now, go through and modify the values in the .env files
        for val in EnvValues:
            try:
                touch(val.envFile)
            except Exception:
                pass

            try:
                oldDotEnvVersion = False
                try:
                    dotenv_imported.set_key(
                        val.envFile,
                        val.key,
                        str(val.value),
                        quote_mode='never',
                        encoding='utf-8',
                    )
                except TypeError:
                    oldDotEnvVersion = True

                if oldDotEnvVersion:
                    dotenv_imported.set_key(
                        val.envFile,
                        val.key,
                        str(val.value),
                        quote_mode='never',
                    )

            except Exception as e:
                eprint(f"Setting value for {val.key} in {val.envFile} module failed ({type(e).__name__}): {e}")

        # change ownership of .envs file to match puid/pgid
        if (
            ((self.platform == PLATFORM_LINUX) or (self.platform == PLATFORM_MAC))
            and (self.scriptUser == "root")
            and (getpwuid(os.stat(args.configDir).st_uid).pw_name == self.scriptUser)
        ):
            if args.debug:
                eprint(f"Setting permissions of {args.configDir} to {puid}:{pgid}")
            os.chown(args.configDir, int(puid), int(pgid))
        envFiles = []
        for exts in ('*.env', '*.env.example'):
            envFiles.extend(glob.glob(os.path.join(args.configDir, exts)))
        for envFile in envFiles:
            if (
                ((self.platform == PLATFORM_LINUX) or (self.platform == PLATFORM_MAC))
                and (self.scriptUser == "root")
                and (getpwuid(os.stat(envFile).st_uid).pw_name == self.scriptUser)
            ):
                if args.debug:
                    eprint(f"Setting permissions of {envFile} to {puid}:{pgid}")
                os.chown(envFile, int(puid), int(pgid))

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            # modify docker-compose specific values (port mappings, volume bind mounts, etc.) in-place in docker-compose files
            for composeFile in configFiles:
                # save off owner of original files
                composeFileStat = os.stat(composeFile)
                origUid, origGuid = composeFileStat[4], composeFileStat[5]
                composeFileHandle = fileinput.FileInput(composeFile, inplace=True, backup=None)
                try:
                    sectionIndents = defaultdict(lambda: '  ')
                    currentSection = None
                    currentService = None
                    networkWritten = False

                    for line in composeFileHandle:
                        line = line.rstrip("\n")
                        skipLine = False
                        sectionStartLine = False
                        serviceStartLine = False

                        # it would be cleaner to use something like PyYAML to do this, but I want to have as few dependencies
                        # as possible so we're going to do it janky instead. Also, as of right now pyyaml doesn't preserve
                        # comments, which is a big deal for this complicated docker-compose file. There is
                        # https://pypi.org/project/ruamel.yaml to possibly consider if we're comfortable with the dependency.

                        # determine which section of the compose file we are in (e.g., services, networks, volumes, etc.)
                        sectionMatch = re.match(r'^([^\s#]+):\s*(#.*)?$', line)
                        if sectionMatch is not None:
                            currentSection = sectionMatch.group(1)
                            sectionStartLine = True
                            currentService = None

                        # determine indentation for each compose file section (assumes YML file is consistently indented)
                        if (currentSection is not None) and (currentSection not in sectionIndents):
                            indentMatch = re.search(r'^(\s+)\S+\s*:\s*$', line)
                            if indentMatch is not None:
                                sectionIndents[currentSection] = indentMatch.group(1)

                        # determine which service we're currently processing in the YML file
                        if currentSection == 'services':
                            serviceMatch = re.search(fr'^{sectionIndents[currentSection]}(\S+)\s*:\s*$', line)
                            if serviceMatch is not None:
                                currentService = serviceMatch.group(1).lower()
                                serviceStartLine = True

                        if (currentSection == 'services') and (not serviceStartLine) and (currentService is not None):
                            # down in the individual services sections of the compose file

                            if re.match(r'^\s*restart\s*:.*$', line):
                                # whether or not to restart services automatically (on boot, etc.)
                                line = f"{sectionIndents[currentSection] * 2}restart: {restartMode}"

                            elif currentService == 'arkime':
                                # stuff specifically in the arkime section
                                if re.match(r'^\s*-.+:/data/pcap(:.+)?\s*$', line):
                                    # Arkime's reference to the PCAP directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        pcapDir,
                                        sectionIndents[currentSection] * 3,
                                    )
                                elif re.match(r'^[\s#]*-\s*"([\d\.]+:)?\d+:\d+"\s*$', line):
                                    # set bind IP based on whether it should be externally exposed or not
                                    line = re.sub(
                                        r'^([\s#]*-\s*")([\d\.]+:)?(\d+:\d+"\s*)$',
                                        fr"\g<1>{'0.0.0.0' if arkimeViewerOpen else '127.0.0.1'}:\g<3>",
                                        line,
                                    )

                            elif currentService == 'filebeat':
                                # stuff specifically in the filebeat section
                                if re.match(r'^[\s#]*-\s*"([\d\.]+:)?\d+:\d+"\s*$', line):
                                    # set bind IP based on whether it should be externally exposed or not
                                    line = re.sub(
                                        r'^([\s#]*-\s*")([\d\.]+:)?(\d+:\d+"\s*)$',
                                        fr"\g<1>{'0.0.0.0' if filebeatTcpOpen else '127.0.0.1'}:\g<3>",
                                        line,
                                    )

                                elif re.match(r'^\s*-.+:/suricata(:.+)?\s*$', line):
                                    # filebeat's reference to the suricata-logs directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        suricataLogDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/zeek(:.+)?\s*$', line):
                                    # filebeat's reference to the zeek-logs directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        zeekLogDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'file-monitor':
                                # stuff specifically in the file-monitor section
                                if re.match(r'^\s*-.+:/zeek/extract_files(:.+)?\s*$', line):
                                    # file-monitor's reference to the zeek-logs/extract_files directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(zeekLogDir, 'extract_files'),
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/zeek/logs(:.+)?\s*$', line):
                                    # zeek's reference to the zeek-logs/current directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(zeekLogDir, 'current'),
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'logstash':
                                # stuff specifically in the logstash section
                                if re.match(r'^[\s#]*-\s*"([\d\.]+:)?\d+:\d+"\s*$', line):
                                    # set bind IP based on whether it should be externally exposed or not
                                    line = re.sub(
                                        r'^([\s#]*-\s*")([\d\.]+:)?(\d+:\d+"\s*)$',
                                        fr"\g<1>{'0.0.0.0' if logstashOpen else '127.0.0.1'}:\g<3>",
                                        line,
                                    )

                            elif currentService == 'opensearch':
                                # stuff specifically in the opensearch section
                                if re.match(r'^\s*-.+:/usr/share/opensearch/data(:.+)?\s*$', line):
                                    # OpenSearch indexes directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        indexDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/opt/opensearch/backup(:.+)?\s*$', line):
                                    # OpenSearch backup directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        indexSnapshotDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'pcap-capture':
                                # stuff specifically in the pcap-capture section
                                if re.match(r'^\s*-.+:/pcap(:.+)?\s*$', line):
                                    # pcap-capture's reference to the PCAP directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(pcapDir, 'upload'),
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'pcap-monitor':
                                # stuff specifically in the pcap-monitor section
                                if re.match(r'^\s*-.+:/pcap(:.+)?\s*$', line):
                                    # pcap-monitor's reference to the PCAP directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        pcapDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/zeek(:.+)?\s*$', line):
                                    # pcap-monitor's reference to the zeek-logs directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        zeekLogDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'suricata':
                                # stuff specifically in the suricata section
                                if re.match(r'^\s*-.+:/data/pcap(:.+)?\s*$', line):
                                    # Suricata's reference to the PCAP directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        pcapDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/var/log/suricata(:.+)?\s*$', line):
                                    # suricata's reference to the suricata-logs directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        suricataLogDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'suricata-live':
                                # stuff specifically in the suricata-live section
                                if re.match(r'^\s*-.+:/var/log/suricata(:.+)?\s*$', line):
                                    # suricata-live's reference to the suricata-logs directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        suricataLogDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'upload':
                                # stuff specifically in the upload section
                                if re.match(r'^[\s#]*-\s*"([\d\.]+:)?\d+:\d+"\s*$', line):
                                    # set bind IP based on whether it should be externally exposed or not
                                    line = re.sub(
                                        r'^([\s#]*-\s*")([\d\.]+:)?(\d+:\d+"\s*)$',
                                        fr"\g<1>{'0.0.0.0' if sftpOpen else '127.0.0.1'}:\g<3>",
                                        line,
                                    )

                                elif re.match(r'^\s*-.+:/var/www/upload/server/php/chroot/files(:.+)?\s*$', line):
                                    # upload's reference to the PCAP directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(pcapDir, 'upload'),
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'zeek':
                                # stuff specifically in the zeek section
                                if re.match(r'^\s*-.+:/pcap(:.+)?\s*$', line):
                                    # Zeek's reference to the PCAP directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        pcapDir,
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/zeek/upload(:.+)?\s*$', line):
                                    # zeek's reference to the zeek-logs/upload directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(zeekLogDir, 'upload'),
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/zeek/extract_files(:.+)?\s*$', line):
                                    # zeek's reference to the zeek-logs/extract_files directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(zeekLogDir, 'extract_files'),
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'zeek-live':
                                # stuff specifically in the zeek-live section
                                if re.match(r'^\s*-.+:/zeek/live(:.+)?\s*$', line):
                                    # zeek-live's reference to the zeek-logs/live directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(zeekLogDir, 'live'),
                                        sectionIndents[currentSection] * 3,
                                    )

                                elif re.match(r'^\s*-.+:/zeek/extract_files(:.+)?\s*$', line):
                                    # zeek-lives's reference to the zeek-logs/extract_files directory
                                    line = ReplaceBindMountLocation(
                                        line,
                                        os.path.join(zeekLogDir, 'extract_files'),
                                        sectionIndents[currentSection] * 3,
                                    )

                            elif currentService == 'nginx-proxy':
                                # stuff specifically in the nginx-proxy section

                                if re.match(r'^\s*test\s*:', line):
                                    # set nginx-proxy health check based on whether they're using HTTPS or not
                                    line = re.sub(
                                        r'https?://localhost:\d+',
                                        fr"{'https' if nginxSSL else 'http'}://localhost:443",
                                        line,
                                    )

                                elif re.match(r'^[\s#]*-\s*"([\d\.]+:)?\d+:\d+"\s*$', line):
                                    # set bind IPs and ports based on whether it should be externally exposed or not
                                    line = re.sub(
                                        r'^([\s#]*-\s*")([\d\.]+:)?(\d+:\d+"\s*)$',
                                        fr"\g<1>{'0.0.0.0' if nginxSSL and (((not '9200:9200' in line) and (not '5601:5601' in line)) or opensearchOpen) else '127.0.0.1'}:\g<3>",
                                        line,
                                    )
                                    if nginxSSL is False:
                                        if ':443:' in line:
                                            line = line.replace(':443:', ':80:')
                                        if ':9200:' in line:
                                            line = line.replace(':9200:', ':9201:')
                                    else:
                                        if ':80:' in line:
                                            line = line.replace(':80:', ':443:')
                                        if ':9201:' in line:
                                            line = line.replace(':9201:', ':9200:')

                                elif 'traefik.' in line:
                                    # enable/disable/configure traefik labels if applicable

                                    # Traefik enabled vs. disabled
                                    if 'traefik.enable' in line:
                                        line = re.sub(
                                            r'(#\s*)?(traefik\.enable\s*:\s*)(\S+)',
                                            fr"\g<2>{TrueOrFalseQuote(behindReverseProxy and traefikLabels)}",
                                            line,
                                        )
                                    else:
                                        line = re.sub(
                                            r'(#\s*)?(traefik\..*)',
                                            fr"{'' if traefikLabels else '# '}\g<2>",
                                            line,
                                        )

                                    if 'traefik.http.' in line and '.osmalcolm.' in line:
                                        # OpenSearch router enabled/disabled/host value
                                        line = re.sub(
                                            r'(#\s*)?(traefik\..*)',
                                            fr"{'' if behindReverseProxy and traefikLabels and opensearchOpen else '# '}\g<2>",
                                            line,
                                        )
                                        if ('.rule') in line:
                                            line = re.sub(
                                                r'(traefik\.http\.routers\.osmalcolm\.rule\s*:\s*)(\S+)',
                                                fr"\g<1>'Host(`{traefikOpenSearchHost}`)'",
                                                line,
                                            )

                                    if 'traefik.http.routers.malcolm.rule' in line:
                                        # Malcolm interface router host value
                                        line = re.sub(
                                            r'(traefik\.http\.routers\.malcolm\.rule\s*:\s*)(\S+)',
                                            fr"\g<1>'Host(`{traefikHost}`)'",
                                            line,
                                        )

                                    elif 'traefik.http.routers.' in line and '.entrypoints' in line:
                                        # Malcolm routers entrypoints
                                        line = re.sub(
                                            r'(traefik\.[\w\.]+\s*:\s*)(\S+)',
                                            fr"\g<1>'{traefikEntrypoint}'",
                                            line,
                                        )

                                    elif 'traefik.http.routers.' in line and '.certresolver' in line:
                                        # Malcolm routers resolvers
                                        line = re.sub(
                                            r'(traefik\.[\w\.]+\s*:\s*)(\S+)',
                                            fr"\g<1>'{traefikResolver}'",
                                            line,
                                        )

                        elif currentSection == 'networks':
                            # re-write the network definition from scratch
                            if not sectionStartLine:
                                if not networkWritten:
                                    print(f"{sectionIndents[currentSection]}default:")
                                    print(
                                        f"{sectionIndents[currentSection] * 2}external: {'true' if (len(dockerNetworkExternalName) > 0) else 'false'}"
                                    )
                                    if len(dockerNetworkExternalName) > 0:
                                        print(f"{sectionIndents[currentSection] * 2}name: {dockerNetworkExternalName}")
                                    networkWritten = True
                                # we already re-wrote the network stuff, anything else is superfluous
                                skipLine = True

                        if not skipLine:
                            print(line)

                finally:
                    composeFileHandle.close()
                    # restore ownership
                    os.chown(composeFile, origUid, origGuid)

        try:
            touch(MalcolmCfgRunOnceFile)
            if ((self.platform == PLATFORM_LINUX) or (self.platform == PLATFORM_MAC)) and (self.scriptUser == "root"):
                os.chown(MalcolmCfgRunOnceFile, int(puid), int(pgid))
        except Exception:
            pass

        # if the Malcolm dir is owned by root, see if they want to reassign ownership to a non-root user
        if (
            ((self.platform == PLATFORM_LINUX) or (self.platform == PLATFORM_MAC))
            and (self.scriptUser == "root")
            and (getpwuid(os.stat(malcolm_install_path).st_uid).pw_name == self.scriptUser)
            and InstallerYesOrNo(
                f'Set ownership of {malcolm_install_path} to an account other than {self.scriptUser}?',
                default=True,
                forceInteraction=True,
            )
        ):
            tmpUser = ''
            while len(tmpUser) == 0:
                tmpUser = InstallerAskForString('Enter user account').strip()
            err, out = self.run_process(['id', '-g', '-n', tmpUser], stderr=True)
            if (err == 0) and (len(out) > 0) and (len(out[0]) > 0):
                tmpUser = f"{tmpUser}:{out[0]}"
            err, out = self.run_process(['chown', '-R', tmpUser, malcolm_install_path], stderr=True)
            if err == 0:
                if self.debug:
                    eprint(f"Changing ownership of {malcolm_install_path} to {tmpUser} succeeded")
            else:
                eprint(f"Changing ownership of {malcolm_install_path} to {tmpUser} failed: {out}")


###################################################################################################
class LinuxInstaller(Installer):
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def __init__(self, orchMode, debug=False, configOnly=False):
        super().__init__(orchMode, debug, configOnly)

        self.distro = None
        self.codename = None
        self.release = None

        # determine the distro (e.g., ubuntu) and code name (e.g., bionic) if applicable

        # check /etc/os-release values first
        if os.path.isfile('/etc/os-release'):
            osInfo = dict()

            with open("/etc/os-release", 'r') as f:
                for line in f:
                    try:
                        k, v = line.rstrip().split("=")
                        osInfo[k] = v.strip('"')
                    except Exception:
                        pass

            if ('NAME' in osInfo) and (len(osInfo['NAME']) > 0):
                self.distro = osInfo['NAME'].lower().split()[0]

            if ('VERSION_CODENAME' in osInfo) and (len(osInfo['VERSION_CODENAME']) > 0):
                self.codename = osInfo['VERSION_CODENAME'].lower().split()[0]

            if ('VERSION_ID' in osInfo) and (len(osInfo['VERSION_ID']) > 0):
                self.release = osInfo['VERSION_ID'].lower().split()[0]

        # try lsb_release next
        if self.distro is None:
            err, out = self.run_process(['lsb_release', '-is'], stderr=False)
            if (err == 0) and (len(out) > 0):
                self.distro = out[0].lower()

        if self.codename is None:
            err, out = self.run_process(['lsb_release', '-cs'], stderr=False)
            if (err == 0) and (len(out) > 0):
                self.codename = out[0].lower()

        if self.release is None:
            err, out = self.run_process(['lsb_release', '-rs'], stderr=False)
            if (err == 0) and (len(out) > 0):
                self.release = out[0].lower()

        # try release-specific files
        if self.distro is None:
            if os.path.isfile('/etc/centos-release'):
                distroFile = '/etc/centos-release'
            if os.path.isfile('/etc/redhat-release'):
                distroFile = '/etc/redhat-release'
            elif os.path.isfile('/etc/issue'):
                distroFile = '/etc/issue'
            else:
                distroFile = None
            if distroFile is not None:
                with open(distroFile, 'r') as f:
                    distroVals = f.read().lower().split()
                    distroNums = [x for x in distroVals if x[0].isdigit()]
                    self.distro = distroVals[0]
                    if (self.release is None) and (len(distroNums) > 0):
                        self.release = distroNums[0]

        if self.distro is None:
            self.distro = "linux"

        if self.debug:
            eprint(
                f"distro: {self.distro}{f' {self.codename}' if self.codename else ''}{f' {self.release}' if self.release else ''}"
            )

        if not self.codename:
            self.codename = self.distro

        # determine packages required by Malcolm itself (not docker, those will be done later)
        if (self.distro == PLATFORM_LINUX_UBUNTU) or (self.distro == PLATFORM_LINUX_DEBIAN):
            self.requiredPackages.extend(['apache2-utils', 'make', 'openssl', 'python3-dialog', 'xz-utils'])
        elif (self.distro == PLATFORM_LINUX_FEDORA) or (self.distro == PLATFORM_LINUX_CENTOS):
            self.requiredPackages.extend(['httpd-tools', 'make', 'openssl', 'python3-dialog', 'xz'])

        # on Linux this script requires root, or sudo, unless we're in local configuration-only mode
        if os.getuid() == 0:
            self.scriptUser = "root"
            self.sudoCmd = []
        else:
            self.sudoCmd = ["sudo", "-n"]
            err, out = self.run_process(['whoami'], privileged=True)
            if ((err != 0) or (len(out) == 0) or (out[0] != 'root')) and (not self.configOnly):
                raise Exception(f'{ScriptName} must be run as root, or {self.sudoCmd} must be available')

        # determine command to use to query if a package is installed
        if which('dpkg', debug=self.debug):
            os.environ["DEBIAN_FRONTEND"] = "noninteractive"
            self.checkPackageCmds.append(['dpkg', '-s'])
        elif which('rpm', debug=self.debug):
            self.checkPackageCmds.append(['rpm', '-q'])
        elif which('dnf', debug=self.debug):
            self.checkPackageCmds.append(['dnf', 'list', 'installed'])
        elif which('yum', debug=self.debug):
            self.checkPackageCmds.append(['yum', 'list', 'installed'])

        # determine command to install a package from the distro's repos
        if which('apt-get', debug=self.debug):
            self.installPackageCmds.append(['apt-get', 'install', '-y', '-qq'])
        elif which('apt', debug=self.debug):
            self.installPackageCmds.append(['apt', 'install', '-y', '-qq'])
        elif which('dnf', debug=self.debug):
            self.installPackageCmds.append(['dnf', '-y', 'install', '--nobest'])
        elif which('yum', debug=self.debug):
            self.installPackageCmds.append(['yum', '-y', 'install'])

        # determine total system memory
        try:
            totalMemBytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
            self.totalMemoryGigs = math.ceil(totalMemBytes / (1024.0**3))
        except Exception:
            self.totalMemoryGigs = 0.0

        # determine total system memory a different way if the first way didn't work
        if self.totalMemoryGigs <= 0.0:
            err, out = self.run_process(['awk', '/MemTotal/ { printf "%.0f \\n", $2 }', '/proc/meminfo'])
            if (err == 0) and (len(out) > 0):
                totalMemKiloBytes = int(out[0])
                self.totalMemoryGigs = math.ceil(totalMemKiloBytes / (1024.0**2))

        # determine total system CPU cores
        try:
            self.totalCores = os.sysconf('SC_NPROCESSORS_ONLN')
        except Exception:
            self.totalCores = 0

        # determine total system CPU cores a different way if the first way didn't work
        if self.totalCores <= 0:
            err, out = self.run_process(['grep', '-c', '^processor', '/proc/cpuinfo'])
            if (err == 0) and (len(out) > 0):
                self.totalCores = int(out[0])

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def install_docker(self):
        global requests_imported

        result = False

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            # first see if docker is already installed and runnable
            err, out = self.run_process(['docker', 'info'], privileged=True)

            if err == 0:
                result = True

            elif InstallerYesOrNo('"docker info" failed, attempt to install Docker?', default=True):
                if InstallerYesOrNo('Attempt to install Docker using official repositories?', default=True):
                    # install required packages for repo-based install
                    if self.distro == PLATFORM_LINUX_UBUNTU:
                        requiredRepoPackages = [
                            'apt-transport-https',
                            'ca-certificates',
                            'curl',
                            'gnupg-agent',
                            'software-properties-common',
                        ]
                    elif self.distro == PLATFORM_LINUX_DEBIAN:
                        requiredRepoPackages = [
                            'apt-transport-https',
                            'ca-certificates',
                            'curl',
                            'gnupg2',
                            'software-properties-common',
                        ]
                    elif self.distro == PLATFORM_LINUX_FEDORA:
                        requiredRepoPackages = ['dnf-plugins-core']
                    elif self.distro == PLATFORM_LINUX_CENTOS:
                        requiredRepoPackages = ['yum-utils', 'device-mapper-persistent-data', 'lvm2']
                    else:
                        requiredRepoPackages = []

                    if len(requiredRepoPackages) > 0:
                        eprint(f"Installing required packages: {requiredRepoPackages}")
                        self.install_package(requiredRepoPackages)

                    # install docker via repo if possible
                    dockerPackages = []
                    if (
                        (self.distro == PLATFORM_LINUX_UBUNTU) or (self.distro == PLATFORM_LINUX_DEBIAN)
                    ) and self.codename:
                        # for debian/ubuntu, add docker GPG key and check its fingerprint
                        if self.debug:
                            eprint("Requesting docker GPG key for package signing")
                        dockerGpgKey = requests_imported.get(
                            f'https://download.docker.com/linux/{self.distro}/gpg', allow_redirects=True
                        )
                        err, out = self.run_process(
                            ['apt-key', 'add'],
                            stdin=dockerGpgKey.content.decode(sys.getdefaultencoding()),
                            privileged=True,
                            stderr=False,
                        )
                        if err == 0:
                            err, out = self.run_process(
                                ['apt-key', 'fingerprint', DEB_GPG_KEY_FINGERPRINT], privileged=True, stderr=False
                            )

                        # add docker .deb repository
                        if err == 0:
                            if self.debug:
                                eprint("Adding docker repository")
                            err, out = self.run_process(
                                [
                                    'add-apt-repository',
                                    '-y',
                                    '-r',
                                    f'deb [arch=amd64] https://download.docker.com/linux/{self.distro} {self.codename} stable',
                                ],
                                privileged=True,
                            )
                            err, out = self.run_process(
                                [
                                    'add-apt-repository',
                                    '-y',
                                    '-u',
                                    f'deb [arch=amd64] https://download.docker.com/linux/{self.distro} {self.codename} stable',
                                ],
                                privileged=True,
                            )

                        # docker packages to install
                        if err == 0:
                            dockerPackages.extend(
                                ['docker-ce', 'docker-ce-cli', 'docker-compose-plugin', 'containerd.io']
                            )

                    elif self.distro == PLATFORM_LINUX_FEDORA:
                        # add docker fedora repository
                        if self.debug:
                            eprint("Adding docker repository")
                        err, out = self.run_process(
                            [
                                'dnf',
                                'config-manager',
                                '-y',
                                '--add-repo',
                                'https://download.docker.com/linux/fedora/docker-ce.repo',
                            ],
                            privileged=True,
                        )

                        # docker packages to install
                        if err == 0:
                            dockerPackages.extend(
                                ['docker-ce', 'docker-ce-cli', 'docker-compose-plugin', 'containerd.io']
                            )

                    elif self.distro == PLATFORM_LINUX_CENTOS:
                        # add docker centos repository
                        if self.debug:
                            eprint("Adding docker repository")
                        err, out = self.run_process(
                            [
                                'yum-config-manager',
                                '-y',
                                '--add-repo',
                                'https://download.docker.com/linux/centos/docker-ce.repo',
                            ],
                            privileged=True,
                        )

                        # docker packages to install
                        if err == 0:
                            dockerPackages.extend(
                                ['docker-ce', 'docker-ce-cli', 'docker-compose-plugin', 'containerd.io']
                            )

                    else:
                        err, out = None, None

                    if len(dockerPackages) > 0:
                        eprint(f"Installing docker packages: {dockerPackages}")
                        if self.install_package(dockerPackages):
                            eprint("Installation of docker packages apparently succeeded")
                            result = True
                        else:
                            eprint("Installation of docker packages failed")

                # the user either chose not to use the official repos, the official repo installation failed, or there are not official repos available
                # see if we want to attempt using the convenience script at https://get.docker.com (see https://github.com/docker/docker-install)
                if not result and InstallerYesOrNo(
                    'Docker not installed via official repositories. Attempt to install Docker via convenience script (please read https://github.com/docker/docker-install)?',
                    default=False,
                ):
                    tempFileName = os.path.join(self.tempDirName, 'docker-install.sh')
                    if DownloadToFile("https://get.docker.com/", tempFileName, debug=self.debug):
                        os.chmod(tempFileName, 493)  # 493 = 0o755
                        err, out = self.run_process(([tempFileName]), privileged=True)
                        if err == 0:
                            eprint("Installation of docker apparently succeeded")
                            result = True
                        else:
                            eprint(f"Installation of docker failed: {out}")
                    else:
                        eprint(f"Downloading https://get.docker.com/ to {tempFileName} failed")

            if result and ((self.distro == PLATFORM_LINUX_FEDORA) or (self.distro == PLATFORM_LINUX_CENTOS)):
                # centos/fedora don't automatically start/enable the daemon, so do so now
                err, out = self.run_process(['systemctl', 'start', 'docker'], privileged=True)
                if err == 0:
                    err, out = self.run_process(['systemctl', 'enable', 'docker'], privileged=True)
                    if err != 0:
                        eprint(f"Enabling docker service failed: {out}")
                else:
                    eprint(f"Starting docker service failed: {out}")

            # at this point we either have installed docker successfully or we have to give up, as we've tried all we could
            err, out = self.run_process(['docker', 'info'], privileged=True, retry=6, retrySleepSec=5)
            if result and (err == 0):
                if self.debug:
                    eprint('"docker info" succeeded')

                # add non-root user to docker group if required
                usersToAdd = []
                if self.scriptUser == 'root':
                    while InstallerYesOrNo(
                        f"Add {'a' if len(usersToAdd) == 0 else 'another'} non-root user to the \"docker\" group?"
                    ):
                        tmpUser = InstallerAskForString('Enter user account')
                        if len(tmpUser) > 0:
                            usersToAdd.append(tmpUser)
                else:
                    usersToAdd.append(self.scriptUser)

                for user in usersToAdd:
                    err, out = self.run_process(['usermod', '-a', '-G', 'docker', user], privileged=True)
                    if err == 0:
                        if self.debug:
                            eprint(f'Adding {user} to "docker" group succeeded')
                    else:
                        eprint(f'Adding {user} to "docker" group failed')

            elif err != 0:
                result = False
                raise Exception(f'{ScriptName} requires docker, please see {DOCKER_INSTALL_URLS[self.distro]}')

        return result

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def install_docker_compose(self):
        result = False

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            dockerComposeCmd = 'docker-compose'
            if not which(dockerComposeCmd, debug=self.debug):
                if os.path.isfile('/usr/libexec/docker/cli-plugins/docker-compose'):
                    dockerComposeCmd = '/usr/libexec/docker/cli-plugins/docker-compose'
                elif os.path.isfile('/usr/local/bin/docker-compose'):
                    dockerComposeCmd = '/usr/local/bin/docker-compose'

            # first see if docker-compose is already installed and runnable (try non-root and root)
            err, out = self.run_process([dockerComposeCmd, 'version'], privileged=False)
            if err != 0:
                err, out = self.run_process([dockerComposeCmd, 'version'], privileged=True)

            if (err != 0) and InstallerYesOrNo(
                '"docker-compose version" failed, attempt to install docker-compose?', default=True
            ):
                if InstallerYesOrNo('Install docker-compose directly from docker github?', default=True):
                    # download docker-compose from github and put it in /usr/local/bin

                    # need to know some linux platform info
                    unames = []
                    err, out = self.run_process((['uname', '-s']))
                    if (err == 0) and (len(out) > 0):
                        unames.append(out[0].lower())
                    err, out = self.run_process((['uname', '-m']))
                    if (err == 0) and (len(out) > 0):
                        unames.append(out[0].lower())
                    if len(unames) == 2:
                        # download docker-compose from github and save it to a temporary file
                        tempFileName = os.path.join(self.tempDirName, dockerComposeCmd)
                        dockerComposeUrl = f"https://github.com/docker/compose/releases/download/v{DOCKER_COMPOSE_INSTALL_VERSION}/docker-compose-{unames[0]}-{unames[1]}"
                        if DownloadToFile(dockerComposeUrl, tempFileName, debug=self.debug):
                            os.chmod(tempFileName, 493)  # 493 = 0o755, mark as executable
                            # put docker-compose into /usr/local/bin
                            err, out = self.run_process(
                                (['cp', '-f', tempFileName, '/usr/local/bin/docker-compose']), privileged=True
                            )
                            if err == 0:
                                eprint("Download and installation of docker-compose apparently succeeded")
                                dockerComposeCmd = '/usr/local/bin/docker-compose'
                            else:
                                raise Exception(f'Error copying {tempFileName} to /usr/local/bin: {out}')

                        else:
                            eprint(f"Downloading {dockerComposeUrl} to {tempFileName} failed")

                elif InstallerYesOrNo('Install docker-compose via pip (privileged)?', default=False):
                    # install docker-compose via pip (as root)
                    err, out = self.run_process([self.pipCmd, 'install', dockerComposeCmd], privileged=True)
                    if err == 0:
                        eprint("Installation of docker-compose apparently succeeded")
                    else:
                        eprint(f"Install docker-compose via pip failed with {err}, {out}")

                elif InstallerYesOrNo('Install docker-compose via pip (user)?', default=True):
                    # install docker-compose via pip (regular user)
                    err, out = self.run_process([self.pipCmd, 'install', dockerComposeCmd], privileged=False)
                    if err == 0:
                        eprint("Installation of docker-compose apparently succeeded")
                    else:
                        eprint(f"Install docker-compose via pip failed with {err}, {out}")

            # see if docker-compose is now installed and runnable (try non-root and root)
            err, out = self.run_process([dockerComposeCmd, 'version'], privileged=False)
            if err != 0:
                err, out = self.run_process([dockerComposeCmd, 'version'], privileged=True)

            if err == 0:
                result = True
                if self.debug:
                    eprint('"docker-compose version" succeeded')

            else:
                raise Exception(
                    f'{ScriptName} requires docker-compose, please see {DOCKER_COMPOSE_INSTALL_URLS[self.platform]}'
                )

        return result

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def tweak_system_files(self):
        # make some system configuration changes with permission

        ConfigLines = namedtuple("ConfigLines", ["distros", "filename", "prefix", "description", "lines"], rename=False)

        configLinesToAdd = [
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'fs.file-max=',
                'fs.file-max increases allowed maximum for file handles',
                ['# the maximum number of open file handles', 'fs.file-max=2097152'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'fs.inotify.max_user_watches=',
                'fs.inotify.max_user_watches increases allowed maximum for monitored files',
                ['# the maximum number of user inotify watches', 'fs.inotify.max_user_watches=131072'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'fs.inotify.max_queued_events=',
                'fs.inotify.max_queued_events increases queue size for monitored files',
                ['# the inotify event queue size', 'fs.inotify.max_queued_events=131072'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'fs.inotify.max_user_instances=',
                'fs.inotify.max_user_instances increases allowed maximum monitor file watchers',
                ['# the maximum number of user inotify monitors', 'fs.inotify.max_user_instances=512'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'vm.max_map_count=',
                'vm.max_map_count increases allowed maximum for memory segments',
                ['# the maximum number of memory map areas a process may have', 'vm.max_map_count=262144'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'net.core.somaxconn=',
                'net.core.somaxconn increases allowed maximum for socket connections',
                ['# the maximum number of incoming connections', 'net.core.somaxconn=65535'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'vm.swappiness=',
                'vm.swappiness adjusts the preference of the system to swap vs. drop runtime memory pages',
                ['# decrease "swappiness" (swapping out runtime memory vs. dropping pages)', 'vm.swappiness=1'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'vm.dirty_background_ratio=',
                'vm.dirty_background_ratio defines the percentage of system memory fillable with "dirty" pages before flushing',
                [
                    '# the % of system memory fillable with "dirty" pages before flushing',
                    'vm.dirty_background_ratio=40',
                ],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'vm.dirty_background_ratio=',
                'vm.dirty_background_ratio defines the percentage of dirty system memory before flushing',
                ['# maximum % of dirty system memory before committing everything', 'vm.dirty_background_ratio=40'],
            ),
            ConfigLines(
                [],
                '/etc/sysctl.conf',
                'vm.dirty_ratio=',
                'vm.dirty_ratio defines the maximum percentage of dirty system memory before committing everything',
                ['# maximum % of dirty system memory before committing everything', 'vm.dirty_ratio=80'],
            ),
            ConfigLines(
                ['centos', 'core'],
                '/etc/systemd/system.conf.d/limits.conf',
                '',
                '/etc/systemd/system.conf.d/limits.conf increases the allowed maximums for file handles and memlocked segments',
                ['[Manager]', 'DefaultLimitNOFILE=65535:65535', 'DefaultLimitMEMLOCK=infinity'],
            ),
            ConfigLines(
                [
                    'bionic',
                    'cosmic',
                    'disco',
                    'eoan',
                    'focal',
                    'groovy',
                    'hirsute',
                    'impish',
                    'jammy',
                    'kinetic',
                    'lunar',
                    'mantic',
                    'stretch',
                    'buster',
                    'bookworm',
                    'bullseye',
                    'sid',
                    'trixie',
                    'fedora',
                ],
                '/etc/security/limits.d/limits.conf',
                '',
                '/etc/security/limits.d/limits.conf increases the allowed maximums for file handles and memlocked segments',
                ['* soft nofile 65535', '* hard nofile 65535', '* soft memlock unlimited', '* hard memlock unlimited'],
            ),
        ]

        for config in configLinesToAdd:
            if ((len(config.distros) == 0) or (self.codename in config.distros)) and (
                os.path.isfile(config.filename)
                or InstallerYesOrNo(
                    f'\n{config.description}\n{config.filename} does not exist, create it?', default=True
                )
            ):
                confFileLines = (
                    [line.rstrip('\n') for line in open(config.filename)] if os.path.isfile(config.filename) else []
                )

                if (
                    (len(confFileLines) == 0)
                    or (not os.path.isfile(config.filename) and (len(config.prefix) == 0))
                    or (
                        (len(list(filter(lambda x: x.startswith(config.prefix), confFileLines))) == 0)
                        and InstallerYesOrNo(
                            f'\n{config.description}\n{config.prefix} appears to be missing from {config.filename}, append it?',
                            default=True,
                        )
                    )
                ):
                    echoNewLineJoin = '\\n'
                    err, out = self.run_process(
                        [
                            'bash',
                            '-c',
                            f"mkdir -p {os.path.dirname(config.filename)} && echo -n -e '{echoNewLineJoin}{echoNewLineJoin.join(config.lines)}{echoNewLineJoin}' >> '{config.filename}'",
                        ],
                        privileged=True,
                    )


###################################################################################################
class MacInstaller(Installer):
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def __init__(self, orchMode, debug=False, configOnly=False):
        super().__init__(orchMode, debug, configOnly)

        self.sudoCmd = []

        # first see if brew is already installed and runnable
        err, out = self.run_process(['brew', 'info'])
        brewInstalled = err == 0

        if brewInstalled and InstallerYesOrNo('Homebrew is installed: continue with Homebrew?', default=True):
            self.useBrew = True

        else:
            self.useBrew = False
            if (not brewInstalled) and (
                not InstallerYesOrNo('Homebrew is not installed: continue with manual installation?', default=False)
            ):
                raise Exception(
                    f'Follow the steps at {HOMEBREW_INSTALL_URLS[self.platform]} to install Homebrew, then re-run {ScriptName}'
                )

        if self.useBrew:
            # make sure we have brew cask
            err, out = self.run_process(['brew', 'info', 'cask'])
            if err != 0:
                self.install_package(['cask'])
                if err == 0:
                    if self.debug:
                        eprint('"brew install cask" succeeded')
                else:
                    eprint(f'"brew install cask" failed with {err}, {out}')

            err, out = self.run_process(['brew', 'tap', 'homebrew/cask-versions'])
            if err == 0:
                if self.debug:
                    eprint('"brew tap homebrew/cask-versions" succeeded')
            else:
                eprint(f'"brew tap homebrew/cask-versions" failed with {err}, {out}')

            self.checkPackageCmds.append(['brew', 'cask', 'ls', '--versions'])
            self.installPackageCmds.append(['brew', 'cask', 'install'])

        # determine total system memory
        try:
            totalMemBytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
            self.totalMemoryGigs = math.ceil(totalMemBytes / (1024.0**3))
        except Exception:
            self.totalMemoryGigs = 0.0

        # determine total system memory a different way if the first way didn't work
        if self.totalMemoryGigs <= 0.0:
            err, out = self.run_process(['sysctl', '-n', 'hw.memsize'])
            if (err == 0) and (len(out) > 0):
                totalMemBytes = int(out[0])
                self.totalMemoryGigs = math.ceil(totalMemBytes / (1024.0**3))

        # determine total system CPU cores
        try:
            self.totalCores = os.sysconf('SC_NPROCESSORS_ONLN')
        except Exception:
            self.totalCores = 0

        # determine total system CPU cores a different way if the first way didn't work
        if self.totalCores <= 0:
            err, out = self.run_process(['sysctl', '-n', 'hw.ncpu'])
            if (err == 0) and (len(out) > 0):
                self.totalCores = int(out[0])

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def install_docker(self):
        result = False

        if self.orchMode is OrchestrationFramework.DOCKER_COMPOSE:
            # first see if docker is already installed/runnable
            err, out = self.run_process(['docker', 'info'])

            if (err != 0) and self.useBrew and self.package_is_installed(MAC_BREW_DOCKER_PACKAGE):
                # if docker is installed via brew, but not running, prompt them to start it
                eprint(f'{MAC_BREW_DOCKER_PACKAGE} appears to be installed via Homebrew, but "docker info" failed')
                while True:
                    response = InstallerAskForString(
                        'Starting Docker the first time may require user interaction. Please find and start Docker in the Applications folder, then return here and type YES'
                    ).lower()
                    if response == 'yes':
                        break
                err, out = self.run_process(['docker', 'info'], retry=12, retrySleepSec=5)

            # did docker info work?
            if err == 0:
                result = True

            elif InstallerYesOrNo('"docker info" failed, attempt to install Docker?', default=True):
                if self.useBrew:
                    # install docker via brew cask (requires user interaction)
                    dockerPackages = [MAC_BREW_DOCKER_PACKAGE, "docker-compose"]
                    eprint(f"Installing docker packages: {dockerPackages}")
                    if self.install_package(dockerPackages):
                        eprint("Installation of docker packages apparently succeeded")
                        while True:
                            response = InstallerAskForString(
                                'Starting Docker the first time may require user interaction. Please find and start Docker in the Applications folder, then return here and type YES'
                            ).lower()
                            if response == 'yes':
                                break
                    else:
                        eprint("Installation of docker packages failed")

                else:
                    # install docker via downloaded dmg file (requires user interaction)
                    dlDirName = f'/Users/{self.scriptUser}/Downloads'
                    if os.path.isdir(dlDirName):
                        tempFileName = os.path.join(dlDirName, 'Docker.dmg')
                    else:
                        tempFileName = os.path.join(self.tempDirName, 'Docker.dmg')
                    if DownloadToFile(
                        'https://download.docker.com/mac/edge/Docker.dmg', tempFileName, debug=self.debug
                    ):
                        while True:
                            response = InstallerAskForString(
                                f'Installing and starting Docker the first time may require user interaction. Please open Finder and install {tempFileName}, start Docker from the Applications folder, then return here and type YES'
                            ).lower()
                            if response == 'yes':
                                break

                # at this point we either have installed docker successfully or we have to give up, as we've tried all we could
                err, out = self.run_process(['docker', 'info'], retry=12, retrySleepSec=5)
                if err == 0:
                    result = True
                    if self.debug:
                        eprint('"docker info" succeeded')

                elif err != 0:
                    raise Exception(
                        f'{ScriptName} requires docker edge, please see {DOCKER_INSTALL_URLS[self.platform]}'
                    )

            elif err != 0:
                raise Exception(f'{ScriptName} requires docker edge, please see {DOCKER_INSTALL_URLS[self.platform]}')

            # tweak CPU/RAM usage for Docker in Mac
            settingsFile = MAC_BREW_DOCKER_SETTINGS.format(self.scriptUser)
            if (
                result
                and os.path.isfile(settingsFile)
                and InstallerYesOrNo(f'Configure Docker resource usage in {settingsFile}?', default=True)
            ):
                # adjust CPU and RAM based on system resources
                if self.totalCores >= 16:
                    newCpus = 12
                elif self.totalCores >= 12:
                    newCpus = 8
                elif self.totalCores >= 8:
                    newCpus = 6
                elif self.totalCores >= 4:
                    newCpus = 4
                else:
                    newCpus = 2

                if self.totalMemoryGigs >= 64.0:
                    newMemoryGiB = 32
                elif self.totalMemoryGigs >= 32.0:
                    newMemoryGiB = 24
                elif self.totalMemoryGigs >= 24.0:
                    newMemoryGiB = 16
                elif self.totalMemoryGigs >= 16.0:
                    newMemoryGiB = 12
                elif self.totalMemoryGigs >= 8.0:
                    newMemoryGiB = 8
                elif self.totalMemoryGigs >= 4.0:
                    newMemoryGiB = 4
                else:
                    newMemoryGiB = 2

                while not InstallerYesOrNo(
                    f"Setting {newCpus if newCpus else '(unchanged)'} for CPU cores and {newMemoryGiB if newMemoryGiB else '(unchanged)'} GiB for RAM. Is this OK?",
                    default=True,
                ):
                    newCpus = InstallerAskForString('Enter Docker CPU cores (e.g., 4, 8, 16)')
                    newMemoryGiB = InstallerAskForString('Enter Docker RAM MiB (e.g., 8, 16, etc.)')

                if newCpus or newMemoryGiB:
                    with open(settingsFile, 'r+') as f:
                        data = json.load(f)
                        if newCpus:
                            data['cpus'] = int(newCpus)
                        if newMemoryGiB:
                            data['memoryMiB'] = int(newMemoryGiB) * 1024
                        f.seek(0)
                        json.dump(data, f, indent=2)
                        f.truncate()

                    # at this point we need to essentially update our system memory stats because we're running inside docker
                    # and don't have the whole banana at our disposal
                    self.totalMemoryGigs = newMemoryGiB

                    eprint("Docker resource settings adjusted, attempting restart...")

                    err, out = self.run_process(['osascript', '-e', 'quit app "Docker"'])
                    if err == 0:
                        time.sleep(5)
                        err, out = self.run_process(['open', '-a', 'Docker'])

                    if err == 0:
                        err, out = self.run_process(['docker', 'info'], retry=12, retrySleepSec=5)
                        if err == 0:
                            if self.debug:
                                eprint('"docker info" succeeded')

                    else:
                        eprint(f"Restarting Docker automatically failed: {out}")
                        while True:
                            response = InstallerAskForString(
                                'Please restart Docker via the system taskbar, then return here and type YES'
                            ).lower()
                            if response == 'yes':
                                break

        return result


###################################################################################################
# main
def main():
    global args
    global requests_imported
    global kube_imported
    global yaml_imported
    global dotenv_imported

    # extract arguments from the command line
    # print (sys.argv[1:]);
    parser = argparse.ArgumentParser(
        description='Malcolm install script', add_help=False, usage=f'{ScriptName} <arguments>'
    )
    parser.add_argument(
        '-v',
        '--verbose',
        dest='debug',
        type=str2bool,
        nargs='?',
        metavar="true|false",
        const=True,
        default=False,
        help="Verbose output",
    )
    parser.add_argument(
        '-d',
        '--defaults',
        dest='acceptDefaultsNonInteractive',
        type=str2bool,
        nargs='?',
        metavar="true|false",
        const=True,
        default=False,
        help="Accept defaults to prompts without user interaction",
    )
    parser.add_argument(
        '-c',
        '--configure',
        dest='configOnly',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Only do configuration (not installation)",
    )

    configDomainArgGroup = parser.add_argument_group('Configuration files')
    configDomainArgGroup.add_argument(
        '-f',
        '--configure-file',
        required=False,
        dest='configFile',
        metavar='<string>',
        type=str,
        default=os.getenv('MALCOLM_COMPOSE_FILE', ''),
        help='YAML file (docker-compose file to configure or kubeconfig file)',
    )
    configDomainArgGroup.add_argument(
        '-e',
        '--environment-dir',
        required=False,
        dest='configDir',
        metavar='<string>',
        type=str,
        default=os.getenv('MALCOLM_CONFIG_DIR', None),
        help="Directory containing Malcolm's .env files",
    )

    installFilesArgGroup = parser.add_argument_group('Installation files')
    installFilesArgGroup.add_argument(
        '-m',
        '--malcolm-file',
        required=False,
        dest='mfile',
        metavar='<string>',
        type=str,
        default='',
        help='Malcolm .tar.gz file for installation',
    )
    installFilesArgGroup.add_argument(
        '-i',
        '--image-file',
        required=False,
        dest='ifile',
        metavar='<string>',
        type=str,
        default='',
        help='Malcolm docker images .tar.gz file for installation',
    )

    runtimeOptionsArgGroup = parser.add_argument_group('Runtime options')
    runtimeOptionsArgGroup.add_argument(
        '--malcolm-profile',
        dest='malcolmProfile',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help="Run all Malcolm containers (true) vs. run capture-only containers (false)",
    )
    runtimeOptionsArgGroup.add_argument(
        '--dark-mode',
        dest='dashboardsDarkMode',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help="Enable dark mode for OpenSearch Dashboards",
    )

    authencOptionsArgGroup = parser.add_argument_group('Entryption and authentication options')
    authencOptionsArgGroup.add_argument(
        '--https',
        dest='nginxSSL',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help="Require encrypted HTTPS connections",
    )
    authencOptionsArgGroup.add_argument(
        '--ldap',
        dest='authModeLDAP',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Use Lightweight Directory Access Protocol (LDAP)",
    )
    authencOptionsArgGroup.add_argument(
        '--ldap-mode',
        dest='ldapServerType',
        required=False,
        metavar='<openldap|winldap>',
        type=str,
        default=None,
        help='LDAP server compatibility type',
    )
    authencOptionsArgGroup.add_argument(
        '--ldap-start-tls',
        dest='ldapStartTLS',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Use StartTLS (rather than LDAPS) for LDAP connection security",
    )

    dockerOptionsArgGroup = parser.add_argument_group('Docker options')
    dockerOptionsArgGroup.add_argument(
        '-r',
        '--restart-malcolm',
        dest='malcolmAutoRestart',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Restart Malcolm on system restart (unless-stopped)",
    )
    dockerOptionsArgGroup.add_argument(
        '--reverse-proxied',
        dest='behindReverseProxy',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Malcolm will be running behind another reverse proxy (Traefik, Caddy, etc.)",
    )
    dockerOptionsArgGroup.add_argument(
        '--traefik-host',
        dest='traefikHost',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Request domain (host header value) for Malcolm interface Traefik router (e.g., malcolm.example.org)',
    )
    dockerOptionsArgGroup.add_argument(
        '--traefik-host-opensearch',
        dest='traefikOpenSearchHost',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Request domain (host header value) for OpenSearch Traefik router (e.g., opensearch.malcolm.example.org)',
    )
    dockerOptionsArgGroup.add_argument(
        '--traefik-entrypoint',
        dest='traefikEntrypoint',
        required=False,
        metavar='<string>',
        type=str,
        default='websecure',
        help='Traefik router entrypoint (e.g., websecure)',
    )
    dockerOptionsArgGroup.add_argument(
        '--traefik-resolver',
        dest='traefikResolver',
        required=False,
        metavar='<string>',
        type=str,
        default='myresolver',
        help='Traefik router resolver (e.g., myresolver)',
    )
    dockerOptionsArgGroup.add_argument(
        '--docker-network-name',
        dest='dockerNetworkName',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='External Docker network name (or leave blank for default networking)',
    )

    opensearchArgGroup = parser.add_argument_group('OpenSearch options')
    opensearchArgGroup.add_argument(
        '--opensearch',
        dest='opensearchPrimaryMode',
        required=False,
        metavar='<string>',
        type=str,
        default=DATABASE_MODE_LABELS[DatabaseMode.OpenSearchLocal],
        help=f'Primary OpenSearch mode ({", ".join(list(DATABASE_MODE_ENUMS.keys()))})',
    )
    opensearchArgGroup.add_argument(
        '--opensearch-memory',
        dest='osMemory',
        required=False,
        metavar='<string>',
        type=str,
        default=None,
        help='Memory for OpenSearch (e.g., 16g, 9500m, etc.)',
    )
    opensearchArgGroup.add_argument(
        '--opensearch-url',
        dest='opensearchPrimaryUrl',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Primary remote OpenSearch connection URL',
    )
    opensearchArgGroup.add_argument(
        '--opensearch-ssl-verify',
        dest='opensearchPrimarySslVerify',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Require SSL certificate validation for communication with primary OpenSearch instance",
    )
    opensearchArgGroup.add_argument(
        '--opensearch-compress-snapshots',
        dest='indexSnapshotCompressed',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Compress OpenSearch index snapshots",
    )
    opensearchArgGroup.add_argument(
        '--opensearch-secondary',
        dest='opensearchSecondaryMode',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help=f'Secondary OpenSearch mode to forward Logstash logs to a remote OpenSearch instance',
    )
    opensearchArgGroup.add_argument(
        '--opensearch-secondary-url',
        dest='opensearchSecondaryUrl',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Secondary remote OpenSearch connection URL',
    )
    opensearchArgGroup.add_argument(
        '--opensearch-secondary-ssl-verify',
        dest='opensearchSecondarySslVerify',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Require SSL certificate validation for communication with secondary OpenSearch instance",
    )
    opensearchArgGroup.add_argument(
        '--dashboards-url',
        dest='dashboardsUrl',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Remote OpenSearch Dashboards connection URL',
    )

    logstashArgGroup = parser.add_argument_group('Logstash options')
    logstashArgGroup.add_argument(
        '--logstash-memory',
        dest='lsMemory',
        required=False,
        metavar='<string>',
        type=str,
        default=None,
        help='Memory for Logstash (e.g., 4g, 2500m, etc.)',
    )
    logstashArgGroup.add_argument(
        '--logstash-workers',
        dest='lsWorkers',
        required=False,
        metavar='<integer>',
        type=int,
        default=None,
        help='Number of Logstash workers (e.g., 4, 8, etc.)',
    )
    opensearchArgGroup.add_argument(
        '--logstash-host',
        dest='logstashHost',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Logstash host and port (for when running "capture-only" profile; e.g., 192.168.1.123:5044)',
    )

    openPortsArgGroup = parser.add_argument_group('Expose ports')
    openPortsArgGroup.add_argument(
        '--logstash-expose',
        dest='exposeLogstash',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Expose Logstash port to external hosts",
    )
    openPortsArgGroup.add_argument(
        '--opensearch-expose',
        dest='exposeOpenSearch',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Expose OpenSearch port to external hosts",
    )
    openPortsArgGroup.add_argument(
        '--filebeat-tcp-expose',
        dest='exposeFilebeatTcp',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Expose Filebeat TCP port to external hosts",
    )
    openPortsArgGroup.add_argument(
        '--arkime-viewer-expose',
        dest='exposeArkimeViewer',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Expose Arkime viewer to external hosts for PCAP payload retrieval",
    )
    openPortsArgGroup.add_argument(
        '--sftp-expose',
        dest='exposeSFTP',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Expose SFTP server (for PCAP upload) to external hosts",
    )

    storageArgGroup = parser.add_argument_group('Storage options')
    storageArgGroup.add_argument(
        '--pcap-path',
        dest='pcapDir',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='PCAP storage directory',
    )
    storageArgGroup.add_argument(
        '--zeek-path',
        dest='zeekLogDir',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Zeek log storage directory',
    )
    storageArgGroup.add_argument(
        '--suricata-path',
        dest='suricataLogDir',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Suricata log storage directory',
    )
    storageArgGroup.add_argument(
        '--opensearch-path',
        dest='indexDir',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='OpenSearch index directory',
    )
    storageArgGroup.add_argument(
        '--opensearch-snapshot-path',
        dest='indexSnapshotDir',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='OpenSearch snapshot directory',
    )
    storageArgGroup.add_argument(
        '--delete-old-pcap',
        dest='arkimeManagePCAP',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Arkime should delete PCAP files based on available storage (see https://arkime.com/faq#pcap-deletion)",
    )
    storageArgGroup.add_argument(
        '--delete-index-threshold',
        dest='indexPruneSizeLimit',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help=f'Delete the oldest indices when the database exceeds this threshold (e.g., 250GB, 1TB, 60٪, etc.)',
    )

    analysisArgGroup = parser.add_argument_group('Analysis options')
    analysisArgGroup.add_argument(
        '--auto-suricata',
        dest='autoSuricata',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help="Automatically analyze all PCAP files with Suricata",
    )
    analysisArgGroup.add_argument(
        '--suricata-rule-update',
        dest='suricataRuleUpdate',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Automatically analyze all PCAP files with Suricata",
    )
    analysisArgGroup.add_argument(
        '--auto-zeek',
        dest='autoZeek',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help="Automatically analyze all PCAP files with Zeek",
    )
    analysisArgGroup.add_argument(
        '--zeek-ics',
        dest='zeekIcs',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Malcolm is being used to monitor an Industrial Control Systems (ICS) or Operational Technology (OT) network",
    )
    analysisArgGroup.add_argument(
        '--zeek-ics-best-guess',
        dest='zeekICSBestGuess',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help='Use "best guess" to identify potential OT/ICS traffic with Zeek',
    )
    analysisArgGroup.add_argument(
        '--reverse-dns',
        dest='reverseDns',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help='Perform reverse DNS lookup locally for source and destination IP addresses in logs',
    )
    analysisArgGroup.add_argument(
        '--auto-oui',
        dest='autoOui',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help='Perform hardware vendor OUI lookups for MAC addresses',
    )
    analysisArgGroup.add_argument(
        '--auto-freq',
        dest='autoFreq',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help='Perform string randomness scoring on some fields',
    )

    fileCarveArgGroup = parser.add_argument_group('File extraction options')
    fileCarveArgGroup.add_argument(
        '--file-extraction',
        dest='fileCarveMode',
        required=False,
        metavar='<none|known|mapped|all|interesting>',
        type=str,
        default='none',
        help='Zeek file extraction behavior',
    )
    fileCarveArgGroup.add_argument(
        '--file-preservation',
        dest='filePreserveMode',
        required=False,
        metavar='<none|quarantined|all>',
        type=str,
        default='none',
        help='Zeek file preservation behavior',
    )
    fileCarveArgGroup.add_argument(
        '--extracted-file-server',
        dest='fileCarveHttpServer',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help='Expose web interface for downloading preserved files',
    )
    fileCarveArgGroup.add_argument(
        '--extracted-file-server-password',
        dest='fileCarveHttpServeEncryptKey',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='AES-256-CBC encryption password for downloaded preserved files (blank for unencrypted)',
    )
    fileCarveArgGroup.add_argument(
        '--extracted-file-clamav',
        dest='clamAvScan',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help='Scan extracted files with ClamAV',
    )
    fileCarveArgGroup.add_argument(
        '--extracted-file-yara',
        dest='yaraScan',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help='Scan extracted files with Yara',
    )
    fileCarveArgGroup.add_argument(
        '--extracted-file-capa',
        dest='capaScan',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help='Scan extracted files with Capa',
    )
    fileCarveArgGroup.add_argument(
        '--virustotal-api-key',
        dest='vtotApiKey',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='VirusTotal API key to scan extracted files with VirusTotal',
    )
    fileCarveArgGroup.add_argument(
        '--file-scan-rule-update',
        dest='fileScanRuleUpdate',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Download updated file scanner signatures periodically",
    )

    netboxArgGroup = parser.add_argument_group('NetBox options')
    netboxArgGroup.add_argument(
        '--netbox',
        dest='netboxEnabled',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Run and maintain an instance of NetBox",
    )
    netboxArgGroup.add_argument(
        '--netbox-enrich',
        dest='netboxLogstashEnrich',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=True,
        help="Enrich network traffic using NetBox",
    )
    netboxArgGroup.add_argument(
        '--netbox-autopopulate',
        dest='netboxLogstashAutoPopulate',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Automatically populate NetBox inventory based on observed network traffic",
    )
    netboxArgGroup.add_argument(
        '--netbox-site-name',
        dest='netboxSiteName',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Default NetBox site name',
    )

    captureArgGroup = parser.add_argument_group('Live traffic capture options')
    captureArgGroup.add_argument(
        '--live-capture-iface',
        dest='pcapIface',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Capture interface(s) (comma-separated)',
    )
    captureArgGroup.add_argument(
        '--live-capture-filter',
        dest='pcapFilter',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='Capture filter (tcpdump-like filter expression; leave blank to capture all traffic)',
    )
    captureArgGroup.add_argument(
        '--live-capture-iface-tweak',
        dest='tweakIface',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Disable capture interface hardware offloading and adjust ring buffer sizes",
    )
    captureArgGroup.add_argument(
        '--live-capture-arkime',
        dest='pcapNetSniff',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Capture live network traffic with netsniff-ng for Arkime",
    )
    captureArgGroup.add_argument(
        '--live-capture-arkime-tcpdump',
        dest='pcapTcpDump',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Capture live network traffic with tcpdump for Arkime",
    )
    captureArgGroup.add_argument(
        '--live-capture-zeek',
        dest='liveZeek',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Capture live network traffic with Zeek",
    )
    captureArgGroup.add_argument(
        '--live-capture-suricata',
        dest='liveSuricata',
        type=str2bool,
        metavar="true|false",
        nargs='?',
        const=True,
        default=False,
        help="Capture live network traffic with Suricata",
    )
    captureArgGroup.add_argument(
        '--node-name',
        dest='pcapNodeName',
        required=False,
        metavar='<string>',
        type=str,
        default=os.getenv('HOSTNAME', os.getenv('COMPUTERNAME', platform.node())).split('.')[0],
        help='The node name to associate with network traffic metadata',
    )
    captureArgGroup.add_argument(
        '--node-host',
        dest='pcapNodeHost',
        required=False,
        metavar='<string>',
        type=str,
        default='',
        help='The node hostname or IP address to associate with network traffic metadata',
    )

    try:
        parser.error = parser.exit
        args = parser.parse_args()
    except SystemExit:
        parser.print_help()
        exit(2)

    if os.path.islink(os.path.join(ScriptPath, ScriptName)) and ScriptName.startswith('configure'):
        args.configOnly = True

    if args.debug:
        eprint(os.path.join(ScriptPath, ScriptName))
        eprint(f"Arguments: {sys.argv[1:]}")
        eprint(f"Arguments: {args}")
    else:
        sys.tracebacklimit = 0

    requests_imported = RequestsDynamic(debug=args.debug, forceInteraction=(not args.acceptDefaultsNonInteractive))
    yaml_imported = YAMLDynamic(debug=args.debug, forceInteraction=(not args.acceptDefaultsNonInteractive))
    dotenv_imported = DotEnvDynamic(debug=args.debug, forceInteraction=(not args.acceptDefaultsNonInteractive))
    if args.debug:
        eprint(f"Imported requests: {requests_imported}")
        eprint(f"Imported yaml: {yaml_imported}")
        eprint(f"Imported dotenv: {dotenv_imported}")
    if (not requests_imported) or (not yaml_imported) or (not dotenv_imported):
        exit(2)

    orchMode = OrchestrationFramework.UNKNOWN
    if args.configFile and os.path.isfile(args.configFile):
        if not (
            (orchMode := DetermineYamlFileFormat(args.configFile)) and (orchMode in OrchestrationFrameworksSupported)
        ):
            raise Exception(f'{args.configFile} must be a docker-compose or kubeconfig YAML file')
    else:
        orchMode = OrchestrationFramework.DOCKER_COMPOSE

    # If Malcolm and images tarballs are provided, we will use them.
    # If they are not provided, look in the pwd first, then in the script directory, to see if we
    # can locate the most recent tarballs
    malcolmFile = None
    imageFile = None

    if args.mfile and os.path.isfile(args.mfile):
        malcolmFile = args.mfile
    else:
        # find the most recent non-image tarball, first checking in the pwd then in the script path
        files = list(filter(lambda x: "_images" not in x, glob.glob(os.path.join(origPath, '*.tar.gz'))))
        if len(files) == 0:
            files = list(filter(lambda x: "_images" not in x, glob.glob(os.path.join(ScriptPath, '*.tar.gz'))))
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        if len(files) > 0:
            malcolmFile = files[0]

    if args.ifile and os.path.isfile(args.ifile):
        imageFile = args.ifile

    if (malcolmFile and os.path.isfile(malcolmFile)) and (not imageFile or not os.path.isfile(imageFile)):
        # if we've figured out the malcolm tarball, the _images tarball should match it
        imageFile = malcolmFile.replace('.tar.gz', '_images.tar.xz')
        if not os.path.isfile(imageFile):
            imageFile = None

    if args.debug:
        if args.configOnly:
            eprint("Only doing configuration, not installation")
        else:
            eprint(f"Malcolm install file: {malcolmFile}")
            eprint(f"Docker images file: {imageFile}")

    installerPlatform = platform.system()
    if installerPlatform == PLATFORM_LINUX:
        installer = LinuxInstaller(orchMode, debug=args.debug, configOnly=args.configOnly)
    elif installerPlatform == PLATFORM_MAC:
        installer = MacInstaller(orchMode, debug=args.debug, configOnly=args.configOnly)
    elif installerPlatform == PLATFORM_WINDOWS:
        raise Exception(f'{ScriptName} is not yet supported on {installerPlatform}')
        # installer = WindowsInstaller(orchMode, debug=args.debug, configOnly=args.configOnly)

    success = False
    installPath = None

    if not args.configOnly:
        if hasattr(installer, 'install_required_packages'):
            success = installer.install_required_packages()
        if (orchMode is OrchestrationFramework.DOCKER_COMPOSE) and hasattr(installer, 'install_docker'):
            success = installer.install_docker()
        if (orchMode is OrchestrationFramework.DOCKER_COMPOSE) and hasattr(installer, 'install_docker_compose'):
            success = installer.install_docker_compose()
        if hasattr(installer, 'tweak_system_files'):
            success = installer.tweak_system_files()
        if (orchMode is OrchestrationFramework.DOCKER_COMPOSE) and hasattr(installer, 'install_docker_images'):
            success = installer.install_docker_images(imageFile)
        if (orchMode is OrchestrationFramework.DOCKER_COMPOSE) and hasattr(installer, 'install_malcolm_files'):
            success, installPath = installer.install_malcolm_files(malcolmFile, args.configDir is None)

    # if .env directory is unspecified, use the default ./config directory
    if args.configDir is None:
        args.configDir = os.path.join(MalcolmPath, 'config')
    try:
        os.makedirs(args.configDir)
    except OSError as exc:
        if (exc.errno == errno.EEXIST) and os.path.isdir(args.configDir):
            pass
        else:
            eprint(f"Creating {args.configDir} failed: {exc}, attempting to continue anyway")
    except Exception as e:
        eprint(f"Creating {args.configDir} failed: {e}, attempting to continue anyway")

    if orchMode is OrchestrationFramework.KUBERNETES:
        kube_imported = KubernetesDynamic(debug=args.debug)
        if args.debug:
            eprint(f"Imported kubernetes: {kube_imported}")
        if kube_imported:
            kube_imported.config.load_kube_config(args.configFile)
        else:
            raise Exception(
                f'{ScriptName} requires the official Python client library for kubernetes for {orchMode} mode'
            )

    if (
        args.configOnly
        or (args.configFile and os.path.isfile(args.configFile))
        or (args.configDir and os.path.isdir(args.configDir))
    ):
        if args.configFile and os.path.isfile(args.configFile):
            installPath = os.path.dirname(os.path.realpath(args.configFile))

        elif args.configDir and os.path.isfile(args.configDir):
            installPath = os.path.dirname(os.path.realpath(args.configDir))

        else:
            for testPath in [origPath, ScriptPath, os.path.realpath(os.path.join(ScriptPath, ".."))]:
                if os.path.isfile(os.path.join(testPath, "docker-compose.yml")) or os.path.isdir(
                    os.path.join(testPath, "config")
                ):
                    installPath = testPath
                    break

        success = (installPath is not None) and os.path.isdir(installPath)
        if args.debug:
            eprint(f"Malcolm installation detected at {installPath}")

    if (installPath is not None) and os.path.isdir(installPath) and hasattr(installer, 'tweak_malcolm_runtime'):
        installer.tweak_malcolm_runtime(installPath)
        eprint(f"\nMalcolm has been installed to {installPath}. See README.md for more information.")
        eprint(
            f"Scripts for starting and stopping Malcolm and changing authentication-related settings can be found in {os.path.join(installPath, 'scripts')}."
        )


if __name__ == '__main__':
    main()
