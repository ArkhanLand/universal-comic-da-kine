from comic_downloader.services.marvel_unlimited import MarvelService


def test_matches_marvel_issue_url() -> None:
    service = MarvelService()

    assert service.matches_url("https://www.marvel.com/comics/issue/72984/house_of_x_2019_1")

    assert service.matches_url("https://marvel.com/comics/issue/49123")

    # Not implemented yet
    assert not service.matches_url("72519")

    assert not service.matches_url("https://www.dc.com/comics/foo")
