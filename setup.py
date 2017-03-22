from setuptools import setup, find_packages

if __name__ == '__main__':
    setup(
        name="asyncbreaker",
        packages=find_packages('src'),
        package_dir={'': 'src'},
    )
