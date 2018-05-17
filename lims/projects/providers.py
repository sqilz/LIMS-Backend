from lims.plugins.mounts import PluginMountPoint


class ProjectPluginProvider(object, metaclass=PluginMountPoint):

    def __init__(self, item, *args, **kwargs):
        self.item = item

    def create(self):
        """
        Stub function: called on the creation of an item.
        """
        pass

    def update(self):
        """
        Stub function: called when an item is updated.
        """
        pass

    def view(self):
        """
        Stub function: called when an item is viewed.
        """
        pass


class ProductPluginProvider(object, metaclass=PluginMountPoint):

    def __init__(self, item, *args, **kwargs):
        self.item = item

    def create(self):
        """
        Stub function: called on the creation of an item.
        """
        pass

    def update(self):
        """
        Stub function: called when an item is updated.
        """
        pass

    def view(self):
        """
        Stub function: called when an item is viewed.
        """
        pass
