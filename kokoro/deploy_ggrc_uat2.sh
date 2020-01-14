#!/bin/bash

# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

set -o nounset
set -o errexit

mv /tmpfs/src/gfile/settings_ggrc_uat2 /tmpfs/src/gfile/settings

CURRENT_SCRIPTPATH=$( cd "$(dirname "$0")" ; pwd -P )
export CURRENT_SCRIPTPATH
$CURRENT_SCRIPTPATH/ggrc_release.sh
