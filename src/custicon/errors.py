class CusticonError(Exception):
    pass


class IconNotFoundError(CusticonError):
    pass


class InvalidIconError(CusticonError):
    pass


class InvalidSettingsError(CusticonError, ValueError):
    pass


class FolderError(CusticonError):
    pass


class MetadataError(CusticonError):
    pass


class StoreError(CusticonError):
    pass


class IconSetError(CusticonError):
    pass
