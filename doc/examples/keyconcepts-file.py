# begin path
    path = self.store.downloaded_path(basefile)
    with open(path, mode="wb") as fp:
        fp.write("...")
# end path

# begin open
    with self.store.open_downloaded(path, mode="wb") as fp:
        fp.write("...")
# end open
