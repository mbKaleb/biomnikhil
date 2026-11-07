#!/bin/bash
# The Settings tab opens this in Terminal, which can't pass arguments to a file
# it opens. app/maintenance-mac.sh does the work.
exec /bin/bash "$(dirname "$0")/maintenance-mac.sh" cleanup
