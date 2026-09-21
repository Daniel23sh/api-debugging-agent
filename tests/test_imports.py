def test_packages_import() -> None:
    import agent_service
    import sandbox_api

    assert agent_service
    assert sandbox_api
