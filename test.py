import subprocess
print(subprocess.list2cmdline(['--dir', '"C:\\test path"']))
print(subprocess.list2cmdline(['--dir', 'C:\\test path']))