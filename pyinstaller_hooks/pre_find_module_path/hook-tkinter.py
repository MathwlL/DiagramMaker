def pre_find_module_path(hook_api):
    # The bundled runtime has tkinter, but PyInstaller can mis-detect its Tcl/Tk
    # probe and remove tkinter from module search paths. Keep search paths intact.
    return
