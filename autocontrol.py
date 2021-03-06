
"""
    Use a Korg nonoKONTROL2 to control the weights of an autoencoder. Given
    a learned model. The Korg attaches to the input of the center (topmost
    encoding layer). The faders of the Korg may be used to multiply the
    incoming weights. Use the faders as a multiplier, use the knobs to change
    the range of the faders. Use the solo buttons to reverse the sign of the
    multiplier. An output screen shows in real time the scale and multipler
    values.

    The play  button plays the original audio file (reconstructed from the
    inverse CQFT. The record button plays the inverse resynthesized CQFT
    as output by the autoencoder. The stop button quits the application.
"""
import argparse
import pygame
import pygame.midi
import numpy as np
import os
import sys
from pylearn2.datasets.dense_design_matrix import DenseDesignMatrix
import wave
import multiprocessing
import pyaudio
import struct
import cPickle
import warnings
from bregman.features_base import Features
###############
import types
import icmc
import pylearn2.datasets.preprocessing

NEURONS_PER_BANK = 8
MID_BUF = 1024

os.environ['SDL_VIDEO_WINDOW_POS'] = "0,400"
class Autocontrol(object):
    def __init__(self, Q):
        self.queue = Q
        while True:
            cmd,msg = self.queue.get(True) if not self.queue.empty(
                ) else ['',None]
            if cmd == 'nneurons':
                self.nneurons = msg
                break
        self.nbanks = int(np.ceil(self.nneurons/8.))
        self.curbank = 0
        self.gain = np.ones(self.nneurons)
        self.scale = np.ones(self.nneurons)
        self.mute = np.ones(self.nneurons)
        self.midi_init()
        self.is_processing = 0
        self.tracks = ['Original', 'Resynthesized']
        self.running = True
        self.start_screen()
        
    def midi_init(self):
        pygame.midi.init()
        devcount = pygame.midi.get_count()
        #print('Num of midi devices connected: {0}'.format(devcount))
        for i in range(devcount):
            dev = pygame.midi.get_device_info(i)
            if (dev[1].split()[0] == 'nanoKONTROL2' and
                dev[1].split()[-1] == 'SLIDER/KNOB'):
                self.devid = i
                #print("Using {0}".format(dev[1]))
                self.cont =  pygame.midi.Input(self.devid)

    def start_screen(self):
        self.screen = pygame.display.set_mode([640, 480])
        pygame.display.set_caption('Midi Control Window')
        pygame.font.init()
        self.font = pygame.font.SysFont("Parchment", 24)
        self.update_mult()
        self.update_text()

    def update_text(self):
        self.screen.fill((0,0,0))
        text = self.font.render('Neuron',1,(255,255,255))
        self.screen.blit(text, (20,20))
        text = self.font.render('Gain',1,(255,255,255))
        self.screen.blit(text, (230,20))
        text = self.font.render('Scale',1,(255,255,255))
        self.screen.blit(text, (320,20))
        text = self.font.render('Adj. Value',1,(255,255,255))
        self.screen.blit(text, (420,20))
        text = self.font.render('Mute',1,(255,255,255))
        self.screen.blit(text, (520,20))
        for i in range(NEURONS_PER_BANK):
            n = self.curbank * NEURONS_PER_BANK + i
            text = self.font.render('{0}'.format(n), 1, (255,255,255))
            self.screen.blit(text, (20,(i+2)*30))
            text = self.font.render("%.5f" % self.gain[n], 1,
                                    (255,255,255))
            self.screen.blit(text, (220,(i+2)*30))
            text = self.font.render("%.5f" % self.scale[n], 1, (255,255,255))
            self.screen.blit(text, (320,(i+2)*30))
            text = self.font.render("%.5f" % self.encoded[n], 1, 
                                    (255,255,255))
            self.screen.blit(text, (420,(i+2)*30))
            text = self.font.render("M" if not self.mute[n] else "", 1, 
                                    (255,255,255))
            self.screen.blit(text, (520,(i+2)*30))
        text = self.font.render('Queued Track: {0}'.format(
                self.tracks[self.is_processing]), 1, (255,255,255))
        self.screen.blit(text, (20,(NEURONS_PER_BANK+4)*30))
        pygame.display.flip()

    def run(self):
        while self.running:
            pygame.event.pump()
            while self.cont.poll():
                data = self.cont.read(MID_BUF)
                ctrl = data[-1][0][1]
                val = data[-1][0][2]
                #print(ctrl)
                if ctrl == 58 and val == 127: # Track <
                    self.change_bank(-1)
                if ctrl == 59 and val == 127: # Track >
                    self.change_bank(1)
                if ctrl == 46 and val == 127: # cycle button
                    self.exit()
                if ctrl == 60 and val == 127: # set button 
                    self.toggle_mute_all()
                if ctrl == 43  and val == 127: # <<
                    self.toggle_processing()
                if ctrl == 44  and val == 127: # >>
                    self.toggle_processing()
                if ctrl == 42 and val == 127: # stop button
                    self.stop()
                if ctrl == 41 and val == 127: # play button 
                    self.play()
                if ctrl == 45 and val == 127: # record button 
                    self.reset_all()
                if ctrl >= 0 and ctrl < 8: # faders
                    self.gain[ctrl + NEURONS_PER_BANK*self.curbank
                              ] = val/127.
                    self.update_mult()
                if ctrl >= 16 and ctrl < 24: # knobs
                    self.scale[ctrl-16 + NEURONS_PER_BANK*self.curbank
                               ] = val/127. * 2
                    self.update_mult()
                if ctrl >= 48 and ctrl < 56 and val == 127: # fader mute
                    self.mute_t_neuron(ctrl-48 + NEURONS_PER_BANK*self.curbank)
                if ctrl >= 64 and ctrl <= 71 and val == 127: # fader record
                    self.reset_neuron(ctrl-64 + NEURONS_PER_BANK*self.curbank)

    def change_bank(self, pos):
        self.curbank += pos
        self.curbank %= self.nbanks
        self.update_text()

    def toggle_processing(self):
        self.is_processing += 1
        self.is_processing %= 2
        self.queue.put(['is_processing', self.is_processing])
        self.update_text()

    def reset_all(self):
        self.gain[:] = 1
        self.scale[:] = 1
        self.mute[:] = 1
        self.update_mult()
        self.update_text()

    def toggle_mute_all(self):
        self.mute = np.where(self.mute == 0, 1, 0)
        self.update_mult()
        self.update_text()

    def mute_t_neuron(self, n):
        self.mute[n] += 1
        self.mute[n] %= 2
        self.update_mult()
        self.update_text()

    def reset_neuron(self, n):
        self.gain[n] = 1
        self.scale[n] = 1
        self.mute[n] = 1
        self.update_mult()
        self.update_text()

    def empty(self):
        while self.cont.poll():
            self.cont.read(MID_BUF)

    def update_mult(self):
        self.encoded = self.gain*self.scale*self.mute
        self.queue.put(["mult", self.encoded])
        self.update_text()

    def play(self):
        self.queue.put(["play_pause", None])

    def stop(self):
        self.queue.put(["stop", None])

    def exit(self):
        pygame.quit()
        self.queue.put(['shutdown', None])
        self.running = False

class PlayStreaming(object):
    def __init__(self, model_file, feature_file, preprocess_file, wav_file, 
                 vocoder, queue):
        # The current models have a front end where there is a rectangular
        # window applied to the analysis window and a hamming window applied to
        # the synthesis window. The order should be reversed. The models
        # were potentially unnecessarily learning to spurious high frequency
        # content. This will be updated here when the models are updated
        with open(feature_file, 'r') as f:
            feat = cPickle.load(f)
            self.feat = feat['feature']
            if self.feat == 'cqft':
                self.Q = Features(np.array([]), feat).Q
            self.nfft = feat['nfft']
            self.wfft = feat['wfft']
            self.nhop = feat['nhop']
            self.sample_rate = feat['sample_rate']
            print("{0}\t{1}\t{2}".format(self.nfft,self.wfft,self.nhop))
        self.nolap = self.nfft-self.nhop
        self.win = np.hanning(self.wfft+1)[:-1]
        self.buf = np.zeros(self.nhop)
        self.olap_buf = np.zeros(self.nolap)

        self.queue = queue

        self.model_file = model_file
        self.init_model()

        self.p = pyaudio.PyAudio()
        self.is_processing = 0
        if wav_file is not None:
            self.source = "file"
            self.wav_file = wav_file
            self.wf = wave.open(wav_file, 'rb')
            if self.wf.getframerate() != self.sample_rate:
                warnings.warn("The audio file sample rate does not match the"+
                        "sample_rate of the models")
            channels = self.wf.getnchannels()
            assert(channels==1)
            format = self.p.get_format_from_width(self.wf.getsampwidth())
            input = False
        else:
            self.source = "line_in"
            channels = 1
            format = pyaudio.paInt16
            input = True
            self.m_buff = np.zeros(self.wfft)
        self.stream = self.p.open(rate=self.sample_rate,
                                  channels=channels,
                                  format=format,
                                  input=input,
                                  output=True,
                                  frames_per_buffer=self.nhop,
                                  start=False)
        with open(preprocess_file, 'r') as f:
            self.preprocess = cPickle.load(f)


        ##### The following patch should be removed at some point ######
        if isinstance(self.preprocess, 
                      pylearn2.datasets.preprocessing.Standardize):
            self.preprocess.invert = types.MethodType(icmc.Standardize.invert,
                                                      self.preprocess)
            self.preprocess.__class__ = icmc.Standardize

        if isinstance(self.preprocess, icmc.Pipeline):
            self.preprocess.invert = types.MethodType(icmc.Pipeline.invert, 
                                                      self.preprocess)
            for item in self.preprocess.items:
                if isinstance(item, 
                              pylearn2.datasets.preprocessing.Standardize):
                    item.invert = types.MethodType(icmc.Standardize.invert,
                                                   item)
                    item.__class__ = icmc.Standardize
        ################################################################
        if vocoder:
            self.dphi = (2 * np.pi * self.nhop * 
                         np.arange(self.nfft/2+1)) / self.nfft
            self.phase = np.random.rand(self.nfft/2+1) * 2 * np.pi - np.pi
        self.vocoder = vocoder
        self.playing = False
        self.run()

    def init_model(self):
        with open(self.model_file, 'r') as f:
            model = cPickle.load(f)
        params = []
        # Added for backward compatibility with older models
        if not hasattr(model, 'autoencoders'):
            model.autoencoders = [model]
        for m in model.autoencoders:
            params.append({})
            if m.act_enc is None:
                params[-1]['act_enc'] = 'linear'
            elif hasattr(m.act_enc, 'name') and m.act_enc.name == 'sigmoid':
                params[-1]['act_enc'] = 'sigmoid'
            elif (hasattr(m.act_enc, 'func_name') and 
                  m.act_enc.func_name == 'relu'):
                params[-1]['act_enc'] = 'relu'
            else:
                raise RuntimeError("Model has unknown encoding function.")
            if m.act_dec is None:
                params[-1]['act_dec'] = 'linear'
            elif hasattr(m.act_dec, 'name') and m.act_dec.name == 'sigmoid':
                params[-1]['act_dec'] = 'sigmoid'
            elif (hasattr(m.act_dec, 'func_name') and 
                         m.act_dec.func_name == 'relu'):
                params[-1]['act_dec'] = 'relu'
            else:
                raise RuntimeError("Model has unknown decoding function.")
            for p in m.get_params():
                params[-1][p.name] = p.get_value()
        input_space = model.autoencoders[0].get_input_space().dim
        nneurons = model.autoencoders[-1].get_output_space().dim
        self.params = params
        for params in self.params:
            if 'Wprime' not in params:
                params['Wprime'] = params['W'].T
        self.queue.put(['nneurons',nneurons], False)
        self.input_space = input_space
        self.nneurons = nneurons
        self.model = model
        # Need this instance for proeprocessing data
        self.ds = DenseDesignMatrix(X=np.zeros((1, input_space)))

    def play_frame(self, start=False):
        nfft = self.nfft
        wfft = self.wfft
        nhop = self.nhop
        nolap = self.nolap
        feat = self.feat
        if self.source == "file":
            wf = self.wf
            ix  = wf.tell()
            data = wf.readframes(wfft)
            if len(data) < 2*wfft:
                self.wf.rewind()
                return
            wf.setpos(ix+nhop)
            data = np.array(struct.unpack("h"*wfft, data)) / 32768.
        else:
            self.m_buff = np.roll(self.m_buff, -nhop)
            data = self.stream.read(nhop)
            data = np.array(struct.unpack("h"*nhop, data)) / 32768.
            self.m_buff[-nhop:] = data
            data = self.m_buff
        #data *= (self.win * 2 / 3)        
        fft = np.fft.rfft(data, nfft) / nfft
        X = np.abs(fft)
        if feat == 'cqft':
            X = np.sqrt(np.dot(self.Q, np.atleast_2d(X).T**2)).flatten()
        if self.vocoder:
            phase = self.phase
            self.phase = (self.phase + np.pi + self.dphi) % (2 * np.pi) - np.pi
            #self.phase = self.phase - 2*np.pi*np.round(self.phase/(2*np.pi))
            #self.phase = (self.phase + np.pi) % (2 * np.pi) - np.pi
            #self.phase = self.phase % (2 * np.pi)
            #print(phase.shape)
        else:
            phase = np.angle(fft)
        if self.is_processing:
            X = self.process_frame(X)
        if feat == 'cqft':
            X = np.dot(self.Q.T, np.atleast_2d(X).T).flatten()
        data = np.real(nfft * np.fft.irfft(X * np.exp(1j * phase)))
        data[:wfft] *= (self.win * 2 / 3)
        self.buf[:] = data[:nhop]
        self.buf += self.olap_buf[:nhop]
        self.olap_buf = np.r_[self.olap_buf[nhop:], np.zeros(nhop)]
        self.olap_buf += data[nhop:]
        self.buf = np.where(self.buf > 1.0, 1.0, self.buf)
        self.buf = np.where(self.buf < -1.0, -1.0, self.buf)

    def process_frame(self, X):
        self.ds.X = np.atleast_2d(X)
        self.preprocess.apply(self.ds)
        X = self.ds.X
        for p in self.params:
            X = self.activation(X, p['W'], p['hb'], p['act_enc'])
        X *= self.mult
        for p in self.params[::-1]:
            X = self.activation(X, p['Wprime'], p['vb'], p['act_dec'])
        self.ds.X = X
        self.preprocess.invert(self.ds)
        X = self.ds.X[0]
        return X

    @staticmethod
    def activation(X, W, b, a, **kwargs):
        X = np.dot(X, W) + b
        if a is not None:
            X = getattr(PlayStreaming, a)(X, **kwargs)
        return X
     
    @staticmethod
    def linear(x, **kwargs):
        return x

    @staticmethod
    def sigmoid(x, **kwargs):
        return 1 / (1 + np.exp(-x))

    @staticmethod
    def relu(x, **kwargs):
        return np.where(x > 0, x, 0)

    def play_stream(self):
        self.playing = True
        start = True
        while self.playing:
            self.play_frame(start)
            start = False
            data = self.buf * 32767
            data = struct.pack("h"*(self.nhop), *data)
            self.stream.write(data, self.nhop)
            self.cmd_parse()

    def run(self):
        while True:
            self.cmd_parse()

    def cmd_parse(self):
        cmd,msg = self.queue.get() if not self.queue.empty() else ['',None]
        if cmd != '':
            #print(cmd)
            pass
        if cmd == "play_pause":
            if self.playing:
                self.stream.stop_stream()
                self.playing = False
            else:
                self.stream.start_stream()
                self.play_stream()
        if cmd == "stop":
            self.stream.stop_stream()
            if self.source is "file":
                self.wf.rewind()
            self.playing = False
        if cmd == "shutdown":
            self.shutdown()
        if cmd == "is_processing":
            self.is_processing = msg
        if cmd == "mult":
            self.mult = msg

    def shutdown(self):
        self.stream.stop_stream()
        self.stream.close()
        if self.source is "file":
            self.wf.close()
        self.p.terminate()
        exit(0)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Use a MIDI controller to '+
                                     'play the code layer of a [deep] '+
                                     'autoencoder.')
    parser.add_argument('modelfile', help="a pickled deep composed "+
                        "autoencoder trained with deepAE.py")
    parser.add_argument('featurefile', help="a pickled feature params"+
                        "file storing a dict of the params used to generate "+
                        "the low level features")
    parser.add_argument('preprocessfile', help="a pickled file "+
                        "storing a pylearn2 preprocessor subclass instance")
    parser.add_argument('-a', '--audiofile', help="an audio file to "+
                        "resynthesize or -m to use your computer's input. If "+
                        "none is specified the program uses the default input"+
                        " device.")
    parser.add_argument('-v', '--vocoder', action='store_true', default=False, 
                        help="channel voder if this flag set")
    args = parser.parse_args()
 
    Q = multiprocessing.Queue()
    P = multiprocessing.Process(target=PlayStreaming, 
                                args=(args.modelfile,
                                      args.featurefile,
                                      args.preprocessfile, 
                                      args.audiofile,
                                      args.vocoder,
                                      Q))
    P.start()
    A = Autocontrol(Q)
    A.run()
    sys.exit(0)
