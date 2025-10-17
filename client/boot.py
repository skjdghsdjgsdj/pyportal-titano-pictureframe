import board
#import storage

# turn off the REPL for the built-in display
board.DISPLAY.root_group = None

# use internal storage as a buffer for the SD card when rendering images
#storage.remount("/", readonly = False)