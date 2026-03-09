Documentation
=============

A great place to start for a new developer on Astrodash is to write or edit
documentation (pages like the one you are reading right now!). The process for
editing or adding documentation is relatively straightforward and does not require
you to run the main Astrodash app. All you need is a text editor and Git installed.

The Astrodash documentation is written in `Sphinx <https://www.sphinx-doc.org/en/master/#user-guides>`_
and is built and hosted automatically using `Read the Docs <https://readthedocs.org/>`_.

All the documentation code and text is contained within :code:`docs/`. Once you
have made changes to the documentation you can preview those changes by running,

.. code:: none

    bash run/astrodashctl docs up

Then visit `http://localhost:4001/` or open :code:`docs/build/index.html`
in your web browser to see the changes. Every time you make changes to the
documentation code you have to re-run the above command.
