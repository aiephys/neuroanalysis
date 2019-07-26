from __future__ import division, print_function

import time
import numpy as np
import pyqtgraph as pg
import pyqtgraph.console
from ..fitting.psp import StackedPsp
from .user_test import UserTestUi


class PspFitUI(object):
    """Used to display details of PSP/PSC fitting analysis.
    """
    def __init__(self, title=None):
        self.pw = pg.GraphicsLayoutWidget()
        self.plt1 = self.pw.addPlot(title=title)
        self.plt2 = self.pw.addPlot(row=1, col=0)
        self.plt2.setXLink(self.plt1)
        self.plt3 = self.pw.addPlot(row=2, col=0)
        self.plt3.setXLink(self.plt1)
        
        self.console = pg.console.ConsoleWidget()
        
        self.widget = pg.QtGui.QSplitter(pg.QtCore.Qt.Vertical)
        self.widget.addWidget(self.pw)        
        self.widget.addWidget(self.console)
        self.widget.resize(1000, 900)
        self.widget.setSizes([800, 200])
        self.widget.show()
    
    def clear(self):
        self.plt1.clear()
        self.plt2.clear()
        self.plt3.clear()

    def show_result(self, fit):
        if fit is None:
            return
        if not isinstance(fit, dict):
            fit = fit.best_values
        psp = StackedPsp()
        x = np.linspace(fit['xoffset']-10e-3, fit['xoffset']+30e-3, 5000)
        y = psp.eval(x=x, **fit)
        self.plt1.plot(x, y, pen='g')
        


class PspFitTestUi(UserTestUi):
    """UI for manually pass/failing PSP fitting unit tests.
    """
    def __init__(self):
        expected_display = PspFitUI('expected result')
        current_display = PspFitUI('current result')
        UserTestUi.__init__(self, expected_display, current_display)