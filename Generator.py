import tensorflow as tf
import numpy as np
from tensorflow.keras.layers import *
from tensorflow.keras import Model
from tensorflow.keras.losses import *
from tensorflow.keras import activations
from AutoEncoderModel import *
from STNFunction import *

class generator_model(Model):
    def __init__(self, img_size, batch_size, num_filter=32):
        super(generator_model, self).__init__()
        self.flowNet = autoencoder_model(num_filter,2,img_size,"channels_last","flowNet")
        self.grid_np = np.float32(np.mgrid[-1:1 + 1e-7 :2 / (img_size[0] - 1), -1:1 + 1e-7:2 / (img_size[1] - 1)])
        self.grid_np = np.moveaxis(self.grid_np, [-2,-1], [0, 1])
        self.flow_grid_np = tf.convert_to_tensor(\
                                tf.constant(np.stack([self.grid_np]*batch_size)),
                                                dtype=tf.float32)
        self.test_grid_np = tf.convert_to_tensor(tf.constant(self.grid_np), dtype=tf.float32)
        
        self.maskNet = autoencoder_model(num_filter,1,img_size,"channels_last","maskNet")
        self.refinementNet = autoencoder_model(num_filter,3,img_size,"channels_last","RefinementNet")
        # ------------
        self.removeResidualNet = autoencoder_model(num_filter,3,img_size,"channels_last","RemovalNet")
        
        self.removeconcat = Concatenate(axis=3)
        self.flowconcat = Concatenate(axis=3)
        self.maskconcat = Concatenate(axis=3)
        self.refineconcat = Concatenate(axis=3)
    def call(self, Ax, By, epoch, training=True):
        fake_Ay, By_flow, raw_By_mask, By_warpped, By_mask, raw_fake_Ay, residual_Ay = self.addAttribute(Ax,By,epoch,training)
        fake_Bx = self.removeAttribute(By,raw_By_mask,training)
        
        fakeBx_to_By, fakeAy_flow, raw_fakeAx_mask, _, _, raw_fakeBx_to_By, _ = \
                    self.addAttribute(fake_Bx,fake_Ay,epoch,training)
        fakeAy_to_Ax = self.removeAttribute(fake_Ay,raw_fakeAx_mask,training)
        
        return_items = {}
        return_items['fake_Ay'] = fake_Ay
        return_items['fakeAy_to_Ax'] = fakeAy_to_Ax
        return_items['fakeBx_to_By'] = fakeBx_to_By
        return_items['fake_Bx'] = fake_Bx
        return_items['flows'] = [By_flow, fakeAy_flow]
        return_items['masks'] = [raw_By_mask, raw_fakeAx_mask, By_mask]
        return_items['raw_fake_Ay'] = raw_fake_Ay
        return_items['By_warpped'] = By_warpped
        return_items['residual_Ay'] = residual_Ay
        
        return return_items
    # ----------------------------------------------------------
    def addAttribute(self,Ax,By,epoch,training):
        Ax_flow_front, By_flow_front, By_flow, By_warpped = self.callflowNet(Ax,By)
        raw_Ay, By_mask = self.callmaskNet(Ax,By_warpped)
        return_By_mask = self.warp_flow(By_mask,-By_flow, training)
        Ay, residual_Ay = self.callrefineNet(raw_Ay, By_mask, epoch)
        return Ay, By_flow, return_By_mask, By_warpped, By_mask, raw_Ay, residual_Ay
    
    def removeAttribute(self,By,mask,training):
        rBy = self.removeResidualNet(By)
        Bx = tf.clip_by_value(By + tf.keras.activations.tanh(rBy) * (1-mask),-1,1)
        return Bx
    # ----------------------------------------------------------
    def callflowNet(self, Ax, By, training=True):
        Ax_front = self.flowNet.Encoder(Ax)
        By_front = self.flowNet.Encoder(By)
        fusion_BottleNeck = self.flowconcat([Ax_front, By_front])
        By_flow = self.flowNet.Decoder(fusion_BottleNeck)
        By_warpped = self.warp_flow(By, By_flow, training)
        return Ax_front, By_front, By_flow, By_warpped
    
    def callmaskNet(self,Ax,By_warpped,training=True):
        Ax_front = self.maskNet.Encoder(Ax)
        By_warpped_front = self.maskNet.Encoder(By_warpped)
        bottleneck_fusion = self.maskconcat([Ax_front,By_warpped_front])
        By_mask = self.maskNet.Decoder(bottleneck_fusion)
        By_mask = Activation('sigmoid')(By_mask)
        Ay = self.blend(By_mask,Ax,By_warpped)
        return Ay, By_mask
    def callrefineNet(self,Ay,By_mask,epoch):
        residual_Ay = self.refinementNet(Ay)
        residual_Ay = tf.keras.activations.tanh(residual_Ay) * 0.1
        Ay = tf.clip_by_value(Ay+residual_Ay,-1,1)
        return Ay, residual_Ay
    # ----------------------------------------------------------
    def warp_flow(self, image, flow, training):
        # flow_grid = self.flow_grid_np + flow
        x_s = self.flow_grid_np[:, :, :, 1] + flow[:,:,:,0]
        y_s = self.flow_grid_np[:, :, :, 0] + flow[:,:,:,1]
        warp_image = self.bilinearSampler(image,x_s,y_s)
        return warp_image
    
    def bilinearSampler(self,image,grid_x,grid_y):
        return stn_bilinear_sampler(image,grid_x,grid_y)
    # ----------------------------------------------------------
    def blend(self,mask,a,b):
        return mask*a+(1-mask)*b